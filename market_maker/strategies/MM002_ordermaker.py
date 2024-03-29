
from market_maker.strategies.genericstrategy import GenericStrategy
from market_maker.settings import settings
from market_maker.utils import mm_math
from market_maker.db.quoting_side import *

TAKER_FEE_PCT = 0.00075
MAKER_FEE_PCT = 0.00025
SL_ATR_MULT = 2
RR_RATIO = 3
INTERVAL_ATR_MULT = 0.5
RELIST_INTERVAL_ATR_MULT = 0.55
MAX_NUM_ORDERS_NON_ZERO_POSITION = 2
MAX_NUM_ORDERS_ZERO_POSITION_QUOTING_SIDE_BOTH = 2
MAX_NUM_ORDERS_ZERO_POSITION_QUOTING_SIDE_NON_BOTH = 1


class MM002_OrderMakerStrategy(GenericStrategy):

    def __init__(self, logger, exchange):
        super().__init__(logger, exchange)

    def check_suspend_trading(self):
        pass

    def place_orders(self):
        """Create order items for use in convergence."""
        buy_orders = []
        sell_orders = []

        running_qty = self.exchange.get_delta()
        if running_qty != 0:
            if running_qty > 0:
                sell_orders.append(self.prepare_tp_order(True, abs(running_qty)))
                sell_orders.append(self.prepare_sl_order(True, abs(running_qty)))
            else:
                buy_orders.append(self.prepare_tp_order(False, abs(running_qty)))
                buy_orders.append(self.prepare_sl_order(False, abs(running_qty)))
        else:
            quoting_side = settings.QUOTING_SIDE
            if self.is_quoting_side_ok(True, quoting_side):
                buy_orders.append(self.prepare_order(-1))
            if self.is_quoting_side_ok(False, quoting_side):
                sell_orders.append(self.prepare_order(1))

        return self.converge_orders(buy_orders, sell_orders)

    def override_parameters(self):
        pass

    def update_dynamic_app_settings(self, force_update):
        self.dynamic_settings.update_app_settings(self.curr_market_snapshot, force_update)
        self.override_parameters()

    def calc_sl_price(self):
        return SL_ATR_MULT * self.curr_market_snapshot.atr_pct_5m

    def get_tp_price(self, is_long, instrument, avg_entry_price):
        take_profit_pct = self.calc_sl_price() * RR_RATIO
        if is_long:
            price = avg_entry_price * (1 + take_profit_pct)
        else:
            price = avg_entry_price * (1 - take_profit_pct)
        price = mm_math.toNearest(price, instrument['tickSize'])
        return price

    def get_sl_price(self, is_long, instrument, avg_entry_price):
        stop_loss_pct = self.calc_sl_price()
        if is_long:
            price = avg_entry_price * (1 - stop_loss_pct)
        else:
            price = avg_entry_price * (1 + stop_loss_pct)
        price = mm_math.toNearest(price, instrument['tickSize'])
        return price

    def get_price(self, index, instrument, ticker_last_price):
        price = ticker_last_price * (1 + index * INTERVAL_ATR_MULT * self.curr_market_snapshot.atr_pct_5m)
        price = mm_math.toNearest(price, instrument['tickSize'])
        return price

    def prepare_tp_order(self, is_long, quantity):
        instrument = self.exchange.get_instrument()
        position = self.exchange.get_position()
        avg_entry_price = position['avgEntryPrice']

        price = self.get_tp_price(is_long, instrument, avg_entry_price)

        return {"price": price, "orderQty": quantity, "side": "Sell" if is_long is True else "Buy", "ordType": "Limit", "execInst": "ParticipateDoNotInitiate,ReduceOnly"}

    def prepare_sl_order(self, is_long, quantity):
        instrument = self.exchange.get_instrument()
        position = self.exchange.get_position()
        avg_entry_price = position['avgEntryPrice']

        price = self.get_sl_price(is_long, instrument, avg_entry_price)

        return {"stopPx": price, "orderQty": quantity, "side": "Sell" if is_long is True else "Buy", "ordType": "Stop", "execInst": "Close,LastPrice"}

    def get_quantity(self, is_long):
        if settings.ENV == "live":
            return 100
        else:
            if not is_long:
                return mm_math.roundQuantity(settings.MIN_POSITION)
            else:
                return mm_math.roundQuantity(settings.MAX_POSITION)

    def prepare_order(self, index):
        """Create an order object."""

        instrument = self.exchange.get_instrument()
        symbol = self.exchange.symbol
        ticker = self.exchange.get_ticker(symbol)
        ticker_last_price = ticker["last"]

        if index > 0:
            quantity = self.get_quantity(False)
        elif index < 0:
            quantity = self.get_quantity(True)
        else:
            quantity = 0

        price = self.get_price(index, instrument, ticker_last_price)
        return {"price": price, "orderQty": quantity, "side": "Buy" if index < 0 else "Sell", "ordType": "Limit", "execInst": "ParticipateDoNotInitiate"}

    def get_ticker(self):
        instrument = self.exchange.get_instrument()
        ticker = self.exchange.get_ticker()
        tickSize = instrument['tickSize']
        tickLog = instrument['tickLog']

        # Midpoint, used for simpler order placement.
        self.start_position_mid = ticker["mid"]
        self.logger.debug("{} Ticker: Buy: {}, Sell: {}".format(instrument['symbol'], round(ticker["buy"], tickLog), round(ticker["sell"], tickLog)))
        self.logger.debug('Start Positions: Buy: {}, Sell: {}, Mid: {}'.format(self.start_position_buy, self.start_position_sell, self.start_position_mid))
        return ticker

    def find_order_with_params(self, orders, orderQty, side, ordType):
        lst = list(filter(lambda o: o["orderQty"] == orderQty and o["side"] == side and o["ordType"] == ordType, orders))
        if len(lst) > 0:
            return lst[0]

    def is_price_diff_exceeded_value(self, price1, price2, pct_value):
        return abs((price1 - price2) / price1) > pct_value

    def validate_orders(self, orders, instrument, running_qty, avgEntryPrice, ticker_last_price, quoting_side):
        relist_interval_pct = RELIST_INTERVAL_ATR_MULT * self.curr_market_snapshot.atr_pct_5m

        if running_qty != 0 and len(orders) != MAX_NUM_ORDERS_NON_ZERO_POSITION or \
           running_qty == 0 and quoting_side == QuotingSide.BOTH and len(orders) != MAX_NUM_ORDERS_ZERO_POSITION_QUOTING_SIDE_BOTH or \
           running_qty == 0 and quoting_side != QuotingSide.BOTH and len(orders) != MAX_NUM_ORDERS_ZERO_POSITION_QUOTING_SIDE_NON_BOTH:
            return False

        if running_qty != 0:
            if running_qty > 0:
                tp_order = self.find_order_with_params(orders, running_qty, "Sell", "Limit")
                sl_order = self.find_order_with_params(orders, running_qty, "Sell", "Stop")
                if not tp_order or not sl_order:
                    return False

                tp_desired_price = self.get_tp_price(True, instrument, avgEntryPrice)
                if tp_order["price"] != tp_desired_price:
                    return False

                sl_desired_price = self.get_sl_price(True, instrument, avgEntryPrice)
                if sl_order["stopPx"] != sl_desired_price:
                    return False
            else:
                tp_order = self.find_order_with_params(orders, -running_qty, "Buy", "Limit")
                sl_order = self.find_order_with_params(orders, -running_qty, "Buy", "Stop")
                if not tp_order or not sl_order:
                    return False

                tp_desired_price = self.get_tp_price(False, instrument, avgEntryPrice)
                if tp_order["price"] != tp_desired_price:
                    return False

                sl_desired_price = self.get_sl_price(False, instrument, avgEntryPrice)
                if sl_order["stopPx"] != sl_desired_price:
                    return False
        else:
            quoting_side = settings.QUOTING_SIDE
            buy_quantity = self.get_quantity(True)
            sell_quantity = self.get_quantity(False)
            buy_order = self.find_order_with_params(orders, abs(buy_quantity), "Buy", "Limit")
            sell_order = self.find_order_with_params(orders, abs(sell_quantity), "Sell", "Limit")

            if self.is_quoting_side_ok(True, quoting_side) and not buy_order:
                return False
            if self.is_quoting_side_ok(False, quoting_side) and not sell_order:
                return False

            if buy_order:
                buy_order_desired_price = self.get_price(-1, instrument, ticker_last_price)
                if self.is_price_diff_exceeded_value(buy_order["price"], buy_order_desired_price, relist_interval_pct):
                    return False

            if sell_order:
                sell_order_desired_price = self.get_price(1, instrument, ticker_last_price)
                if self.is_price_diff_exceeded_value(sell_order["price"], sell_order_desired_price, relist_interval_pct):
                    return False

        return True

    def converge_orders(self, buy_orders, sell_orders):
        instrument = self.exchange.get_instrument()
        symbol = self.exchange.symbol
        ticker = self.exchange.get_ticker(symbol)
        ticker_last_price = ticker["last"]
        existing_orders = self.exchange.get_orders()
        to_create = buy_orders + sell_orders
        to_cancel = existing_orders
        running_qty = self.exchange.get_delta()
        quoting_side = settings.QUOTING_SIDE
        position = self.exchange.get_position()
        avgEntryPrice = position['avgEntryPrice']

        is_orders_valid = self.validate_orders(existing_orders, instrument, running_qty, avgEntryPrice, ticker_last_price, quoting_side)

        if not is_orders_valid:
            if len(to_cancel) > 0:
                self.exchange.cancel_bulk_orders(to_cancel)
            self.exchange.create_bulk_orders(to_create)
            self.print_status(True)

    ###
    # Sanity
    ##
    def sanity_check(self):
        """Perform checks before placing orders."""
        # Check if OB is empty - if so, can't quote.
        self.exchange.check_if_orderbook_empty()

        # Ensure market is still open.
        self.exchange.check_market_open()

        # Messaging if the position limits are reached
        if self.long_position_limit_exceeded():
            self.logger.debug("Long delta limit exceeded")
            self.logger.debug("Current Position: {}, Maximum Position: {}".format(self.exchange.get_delta(), settings.MAX_POSITION))

        if self.short_position_limit_exceeded():
            self.logger.debug("Short delta limit exceeded")
            self.logger.debug("Current Position: {}, Minimum Position: {}".format(self.exchange.get_delta(), settings.MIN_POSITION))