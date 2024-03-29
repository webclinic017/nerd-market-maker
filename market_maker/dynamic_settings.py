import logging
import datetime
from datetime import timedelta
from market_maker.utils.log import log_info
from market_maker.settings import settings
from market_maker.exchange import ExchangeInfo
from market_maker.db.db_manager import DatabaseManager
from market_maker.utils import log

logger = log.setup_robot_custom_logger('root')

DEFAULT_MIN_SPREAD_ADJUSTMENT_FACTOR = 0.6
DEFAULT_RELIST_INTERVAL_ADJUSTMENT_FACTOR = 1.2

BITMEX_DEFAULT_LEVERAGE = 100
BITMEX_DEFAULT_INITIAL_MARGIN_BASE_PCT = 0.01
BITMEX_DEFAULT_TAKER_FEE_PCT = 0.00075

BITFINEX_DEFAULT_MAINTENANCE_RATIO_PCT = 0.15
BITFINEX_DISTANCE_TO_LIQUIDATION_PRICE_PCT = 0.25
BITFINEX_TOTAL_POSITION_MARGIN_ADJUST_RATIO = 0.45
BITFINEX_DEFAULT_LEVERAGE = 5

#PARAMS_UPDATE_INTERVAL = 300  # 5 minutes


class DynamicSettings(object):

    def __init__(self, exchange):
        self.logger = logging.getLogger('root')
        self.exchange = exchange

        self.position_margin_pct = 0
        self.order_margin_pct = 0
        self.position_margin_amount = 0
        self.order_margin_amount = 0
        self.default_leverage = 0
        self.initial_margin_base_pct = 0
        self.taker_fee_pct = 0
        self.max_possible_position_margin = 0
        self.max_number_dca_orders = 0
        self.interval_pct = 0
        self.min_spread_pct = 0
        self.relist_interval_pct = 0
        self.max_short_position_ratio = 0
        self.min_position = 0
        self.max_position = 0
        self.order_step_size = 0
        self.order_start_size = 0
        self.order_pairs = 0
        self.distance_to_avg_price_pct = 0
        self.deposit_usage_pct = 0
        self.deposit_usage_intensity = 0
        self.deposit_usage_intensity_pct = 0

        self.params_last_update = datetime.datetime.now() - timedelta(days=1000)
        self.curr_risk_profile_id = ""
        self.curr_risk_level = 1000000
        self.bitfinex_maintenance_ratio_pct = 0
        self.bitfinex_distance_to_liquidation_price_pct = 0
        self.bitfinex_total_position_margin_adjust_ratio = 0

    def initialize_params(self):
        ticker = self.exchange.get_ticker()
        ticker_last_price = ticker["last"]
        margin = self.exchange.get_margin()
        wallet_balance = margin["walletBalance"]
        position = self.exchange.get_position()
        current_qty = position['currentQty']
        avg_entry_price = position['avgEntryPrice']
        distance_to_avg_price_pct = self.get_distance_to_avg_price_pct(current_qty, avg_entry_price, ticker_last_price)
        running_qty = self.exchange.get_delta()
        deposit_usage_pct = self.get_deposit_usage_pct(running_qty)
        risk_profile = self.get_risk_profile(distance_to_avg_price_pct, deposit_usage_pct)
        market_snapshot = DatabaseManager.retrieve_market_snapshot(logger, settings.EXCHANGE, settings.SYMBOL)

        self.update_dynamic_params(market_snapshot, wallet_balance, ticker_last_price, risk_profile)
        self.update_settings_value("MIN_POSITION", self.min_position)
        self.update_settings_value("MAX_POSITION", self.max_position)

        self.curr_risk_profile_id = "N/A"
        self.curr_risk_level = 1000000

        self.log_params(ticker_last_price)

    def update_settings_value(self, key, value):
        if settings[key] != value:
            settings[key] = value

    def update_app_settings(self, market_snapshot, force_update):
        params_updated = self.update_parameters(market_snapshot, force_update)
        if params_updated is True:
            # TODO: Workaround - needs to be reimplemented
            self.update_settings_value("ORDER_PAIRS", self.order_pairs)
            self.update_settings_value("ORDER_START_SIZE", self.order_start_size)
            self.update_settings_value("ORDER_STEP_SIZE", self.order_step_size)
            self.update_settings_value("INTERVAL", self.interval_pct)
            self.update_settings_value("MIN_SPREAD", self.min_spread_pct)
            self.update_settings_value("RELIST_INTERVAL", self.relist_interval_pct)
            self.update_settings_value("MIN_POSITION", self.min_position)
            self.update_settings_value("MAX_POSITION", self.max_position)
            log_info(self.logger, "Updated NerdMarketMaker settings!", False)
        return params_updated

    def get_distance_to_avg_price_pct(self, current_qty, avg_entry_price, last_price):
        result = 0
        if current_qty != 0:
            result = abs((last_price - avg_entry_price) * 100 / last_price)
        return result

    def get_deposit_usage_pct(self, running_qty):
        if running_qty < 0:
            return abs(running_qty / settings.MIN_POSITION) * 100
        else:
            return abs(running_qty / settings.MAX_POSITION) * 100

    def update_parameters(self, market_snapshot, force_update):
        result = False
        ticker = self.exchange.get_ticker()
        ticker_last_price = ticker["last"]
        margin = self.exchange.get_margin()
        wallet_balance = margin["walletBalance"]
        running_qty = self.exchange.get_delta()
        position = self.exchange.get_position()
        current_qty = position['currentQty']
        avg_entry_price = position['avgEntryPrice']
        self.distance_to_avg_price_pct = self.get_distance_to_avg_price_pct(current_qty, avg_entry_price, ticker_last_price)
        self.deposit_usage_pct = self.get_deposit_usage_pct(running_qty)
        curr_time = datetime.datetime.now()

        #params_seconds_from_last_update = (curr_time - self.params_last_update).total_seconds()
        risk_profile = self.get_risk_profile(self.distance_to_avg_price_pct, self.deposit_usage_pct)
        risk_profile_id = risk_profile.rp_id

        #is_params_exceeded_update_interval_flag = params_seconds_from_last_update >= PARAMS_UPDATE_INTERVAL
        is_risk_profile_changed_flag = True if risk_profile_id != self.curr_risk_profile_id else False

        if force_update or is_risk_profile_changed_flag:
            self.update_dynamic_params(market_snapshot, wallet_balance, ticker_last_price, risk_profile)
            self.params_last_update = curr_time
            result = True

        if result:
            self.log_params(ticker_last_price)

        return result

    def update_dynamic_params(self, market_snapshot, last_wallet_balance, ticker_last_price, risk_profile):
        if ExchangeInfo.is_bitmex():
            self.position_margin_pct = settings.BITMEX_DEFAULT_POSITION_MARGIN_TO_WALLET_RATIO_PCT
            self.order_margin_pct = settings.BITMEX_DEFAULT_ORDER_MARGIN_TO_WALLET_RATIO_PCT
            self.default_leverage = BITMEX_DEFAULT_LEVERAGE
            self.initial_margin_base_pct = BITMEX_DEFAULT_INITIAL_MARGIN_BASE_PCT
            self.taker_fee_pct = BITMEX_DEFAULT_TAKER_FEE_PCT
            self.curr_risk_profile_id = risk_profile.rp_id
            self.curr_risk_level = risk_profile.risk_level
            self.max_number_dca_orders = risk_profile.max_number_dca_orders
            self.interval_pct = risk_profile.interval_atr_mult * market_snapshot.atr_pct_1m * settings.INTERVAL_ADJUST_MULT if market_snapshot.atr_pct_1m > 0 else settings.INTERVAL
            self.min_spread_pct = round(self.interval_pct * 2 * DEFAULT_MIN_SPREAD_ADJUSTMENT_FACTOR, 8)
            self.relist_interval_pct = round(self.interval_pct * DEFAULT_RELIST_INTERVAL_ADJUSTMENT_FACTOR, 8)
            self.order_pairs = risk_profile.order_pairs

            self.position_margin_amount = round(last_wallet_balance * self.position_margin_pct, 8)
            self.order_margin_amount = round(last_wallet_balance * self.order_margin_pct, 8)
            self.max_possible_position_margin = round(self.position_margin_amount * self.default_leverage * ticker_last_price)
            self.max_short_position_ratio = round(1 + 1/(self.position_margin_pct * BITMEX_DEFAULT_LEVERAGE), 8)
            self.min_position = round(-1 * self.max_possible_position_margin * self.max_short_position_ratio)
            self.max_position = round(self.max_possible_position_margin)
            self.order_step_size = self.get_order_step_size(last_wallet_balance)
            self.order_start_size = round(self.max_possible_position_margin / self.max_number_dca_orders - self.order_step_size * (self.max_number_dca_orders - 1) / 2)
            self.deposit_usage_intensity = round(self.order_start_size / (100 * self.interval_pct), 8)
            self.deposit_usage_intensity_pct = self.deposit_usage_intensity * 100 / self.max_possible_position_margin if self.max_possible_position_margin != 0 else 0

        elif ExchangeInfo.is_bitfinex():
            self.curr_risk_profile_id = risk_profile.rp_id
            self.curr_risk_level = risk_profile.risk_level
            self.max_number_dca_orders = risk_profile.max_number_dca_orders
            self.interval_pct = risk_profile.interval_atr_mult * market_snapshot.atr_pct_1m * settings.INTERVAL_ADJUST_MULT if market_snapshot.atr_pct_1m > 0 else settings.INTERVAL_PCT
            self.min_spread_pct = round(self.interval_pct * 2 * DEFAULT_MIN_SPREAD_ADJUSTMENT_FACTOR, 8)
            self.relist_interval_pct = round(self.interval_pct * DEFAULT_RELIST_INTERVAL_ADJUSTMENT_FACTOR, 8)
            self.order_pairs = risk_profile.order_pairs
            if self.order_pairs > 5:
                self.order_pairs = 5

            self.bitfinex_maintenance_ratio_pct = BITFINEX_DEFAULT_MAINTENANCE_RATIO_PCT
            self.bitfinex_distance_to_liquidation_price_pct = BITFINEX_DISTANCE_TO_LIQUIDATION_PRICE_PCT
            self.bitfinex_total_position_margin_adjust_ratio = BITFINEX_TOTAL_POSITION_MARGIN_ADJUST_RATIO
            self.position_margin_pct = (1 - self.bitfinex_distance_to_liquidation_price_pct) * self.bitfinex_total_position_margin_adjust_ratio / (1 - self.bitfinex_maintenance_ratio_pct)
            self.position_margin_amount = round(last_wallet_balance * self.position_margin_pct, 8)
            self.default_leverage = BITFINEX_DEFAULT_LEVERAGE
            self.max_possible_position_margin = round(self.position_margin_amount * self.default_leverage)
            self.min_position = round(-1 * self.max_possible_position_margin / ticker_last_price, 8)
            self.max_position = round(self.max_possible_position_margin / ticker_last_price, 8)
            self.order_step_size = self.get_order_step_size(last_wallet_balance)
            self.order_start_size = round(self.max_possible_position_margin / (ticker_last_price * self.max_number_dca_orders) - self.order_step_size * (self.max_number_dca_orders - 1) / 2, 8)
            self.deposit_usage_intensity = round(self.order_start_size * ticker_last_price / (100 * self.interval_pct), 2)
            self.deposit_usage_intensity_pct = self.deposit_usage_intensity * 100 / self.max_possible_position_margin if self.max_possible_position_margin != 0 else 0

    def get_risk_profile(self, distance_to_avg_price_pct, deposit_usage_pct):
        risk_management_bands = DatabaseManager.retrieve_risk_management_bands(logger)
        risk_profiles = DatabaseManager.retrieve_risk_profiles(logger)
        for rmm_entry in risk_management_bands:
            distance_to_avg_price_band_start = rmm_entry.distance_to_avg_price_band_start
            distance_to_avg_price_band_end = rmm_entry.distance_to_avg_price_band_end
            deposit_usage_band_start = rmm_entry.deposit_usage_band_start
            deposit_usage_band_end = rmm_entry.deposit_usage_band_end
            risk_profile_id = rmm_entry.risk_profile

            if distance_to_avg_price_pct >= distance_to_avg_price_band_start and distance_to_avg_price_pct <= distance_to_avg_price_band_end and deposit_usage_pct >= deposit_usage_band_start and deposit_usage_pct <= deposit_usage_band_end:
                for rpc_entry in risk_profiles:
                    id = rpc_entry.rp_id
                    if id == risk_profile_id:
                        return rpc_entry

        raise Exception("Unable to retrieve risk profile configuration for the following parameters: distance_to_avg_price_pct={}, deposit_usage_pct={}".format(distance_to_avg_price_pct, deposit_usage_pct))

    def get_order_step_size(self, last_wallet_balance):
        # TODO: Reimplement later
        return 0

    def append_log_text(self, str, txt):
        return str + txt + "\n"

    def get_pct_value(self, val):
        return "{}%".format(round(val * 100, 2))

    def log_params(self, ticker_last_price):
        txt = self.append_log_text("",  "Dynamic parameters have been updated:")
        txt = self.append_log_text(txt, "interval_pct (RP) = {} ({})".format(self.get_pct_value(self.interval_pct), self.curr_risk_profile_id))
        txt = self.append_log_text(txt, "min/max position = {}/{}".format(self.min_position, self.max_position))
        txt = self.append_log_text(txt, "order_start_size = {}".format(self.order_start_size))
        txt = self.append_log_text(txt, "distance_to_avg_price_pct = {}%".format(round(self.distance_to_avg_price_pct, 2)))
        txt = self.append_log_text(txt, "deposit_usage_pct = {}%".format(round(self.deposit_usage_pct, 2)))
        txt = self.append_log_text(txt, "deposit_usage_intensity (USD/1% interval) = ${}".format(self.deposit_usage_intensity))
        txt = self.append_log_text(txt, "deposit_usage_intensity (USD/1% interval), % = {}%".format(round(self.deposit_usage_intensity_pct, 2)))
        txt = self.append_log_text(txt, "---------------------")
        txt = self.append_log_text(txt, "Last Price = {}".format(ticker_last_price))

        log_info(self.logger, txt, False)
