"""BitMEX API Connector."""
from __future__ import absolute_import
import requests
import time
import datetime
import json
import base64
import uuid
import logging
from market_maker.settings import settings
from market_maker.auth.bitmex import APIKeyAuthWithExpires
from market_maker.utils.bitmex import constants, errors
from market_maker.ws.bitmex.ws_thread import BitMEXWebsocket
from market_maker.exchange import BaseExchange
from market_maker.exchange import ExchangeInfo


# https://www.bitmex.com/api/explorer/
class BitMEX(BaseExchange):

    """BitMEX API Connector."""

    def __init__(self, symbol=None,
                 orderIDPrefix='mm_bitmex_', shouldWSAuth=True, postOnly=False, timeout=7,
                 retries=24, retry_delay=5):
        """Init connector."""
        self.logger = logging.getLogger('root')
        self.base_url = ExchangeInfo.get_baseurl()
        self.symbol = symbol
        self.postOnly = postOnly
        self.apiKey = ExchangeInfo.get_apikey()
        self.apiSecret = ExchangeInfo.get_apisecret()
        if len(orderIDPrefix) > 13:
            raise ValueError("settings.ORDERID_PREFIX must be at most 13 characters long!")
        self.orderIDPrefix = orderIDPrefix
        self.retries = 0  # initialize counter

        # Prepare HTTPS session
        self.session = requests.Session()
        # These headers are always sent
        self.session.headers.update({'user-agent': 'liquidbot-' + constants.VERSION})
        self.session.headers.update({'content-type': 'application/json'})
        self.session.headers.update({'accept': 'application/json'})

        # Create websocket for streaming data
        self.ws = BitMEXWebsocket()
        self.ws.connect(self.base_url, symbol, shouldAuth=shouldWSAuth)

        self.timeout = timeout
        self.max_retries = retries
        self.retry_delay = retry_delay * 60

    def __del__(self):
        self.exit()

    def exit(self):
        self.ws.exit()

    #
    # Public methods
    #

    def check_if_orderbook_empty(self, symbol):
        """This function checks whether the order book is empty"""
        instrument = self.instrument(symbol)
        if instrument['midPrice'] is None:
            raise errors.MarketEmptyError("Orderbook is empty, cannot quote")

    def check_market_open(self, symbol):
        instrument = self.instrument(symbol)
        if instrument["state"] != "Open" and instrument["state"] != "Closed":
            raise errors.MarketClosedError("The instrument %s is not open. State: %s" %
                                           (self.symbol, instrument["state"]))

    def ticker_data(self, symbol=None):
        """Get ticker data."""
        if symbol is None:
            symbol = self.symbol
        return self.ws.get_ticker(symbol)

    def is_open(self):
        """Check that websockets are still open."""
        return not self.ws.exited

    def instrument(self, symbol):
        """Get an instrument's details."""
        return self.ws.get_instrument(symbol)

    #
    # Authentication required methods
    #
    def authentication_required(fn):
        """Annotation for methods that require auth."""
        def wrapped(self, *args, **kwargs):
            if not (self.apiKey):
                msg = "You must be authenticated to use this method"
                raise errors.AuthenticationError(msg)
            else:
                return fn(self, *args, **kwargs)
        return wrapped

    @authentication_required
    def funds(self):
        """Get your current balance."""
        return self.ws.funds()

    @authentication_required
    def position(self, symbol):
        """Get your open position."""
        return self.ws.position(symbol)

    @authentication_required
    def create_bulk_orders(self, orders):
        """Create multiple orders."""
        for order in orders:
            order['clOrdID'] = self.orderIDPrefix + base64.b64encode(uuid.uuid4().bytes).decode('utf8').rstrip('=\n')
            order['symbol'] = self.symbol
            if self.postOnly:
                order['execInst'] = 'ParticipateDoNotInitiate'
        return self._curl_bitmex(path='order/bulk', postdict={'orders': orders}, verb='POST', max_retries=self.max_retries)

    @authentication_required
    def amend_bulk_orders(self, orders):
        """Amend multiple orders."""
        # Note rethrow; if this fails, we want to catch it and re-tick
        return self._curl_bitmex(path='order/bulk', postdict={'orders': orders}, verb='PUT', rethrow_errors=True, max_retries=self.max_retries)

    @authentication_required
    def open_orders(self):
        """Get open orders."""
        return self.ws.open_orders(self.orderIDPrefix)

    @authentication_required
    def http_open_orders(self):
        """Get open orders via HTTP. Used on close to ensure we catch them all."""
        path = "order"
        orders = self._curl_bitmex(
            path=path,
            query={
                'filter': json.dumps({'ordStatus.isTerminated': False, 'symbol': self.symbol}),
                'count': 500
            },
            verb="GET"
        )
        # Only return orders that start with our clOrdID prefix.
        return [o for o in orders if str(o['clOrdID']).startswith(self.orderIDPrefix)]

    @authentication_required
    def cancel_orders(self, orders):
        """Cancel existing orders."""
        path = "order"
        postdict = {
            'orderID': [o.get('orderID') for o in orders],
        }
        return self._curl_bitmex(path=path, postdict=postdict, verb="DELETE")

    def _curl_bitmex(self, path, query=None, postdict=None, timeout=None, verb=None, rethrow_errors=False,
                     max_retries=None):
        """Send a request to BitMEX Servers."""
        # Handle URL
        url = self.base_url + path

        if timeout is None:
            timeout = self.timeout

        # Default to POST if data is attached, GET otherwise
        if not verb:
            verb = 'POST' if postdict else 'GET'

        # By default don't retry POST or PUT. Retrying GET/DELETE is okay because they are idempotent.
        # In the future we could allow retrying PUT, so long as 'leavesQty' is not used (not idempotent),
        # or you could change the clOrdID (set {"clOrdID": "new", "origClOrdID": "old"}) so that an amend
        # can't erroneously be applied twice.
        if max_retries is None:
            max_retries = 0 if verb in ['POST', 'PUT'] else self.max_retries

        # Auth: API Key/Secret
        auth = APIKeyAuthWithExpires(self.apiKey, self.apiSecret)

        def exit_or_throw(e):
            if rethrow_errors:
                raise e
            else:
                exit(settings.FORCE_RESTART_EXIT_STATUS_CODE)

        def retry():
            self.retries += 1
            if self.retries > max_retries:
                raise Exception("Max retry amount of {} on {} ({}) hit, raising.".format(max_retries, path, json.dumps(postdict or '')))
            time.sleep(self.retry_delay)
            return self._curl_bitmex(path, query, postdict, timeout, verb, rethrow_errors, max_retries)

        # Make the request
        response = None
        try:
            self.logger.info("Sending CURL request to %s: %s" % (url, json.dumps(postdict or query or '')))
            req = requests.Request(verb, url, json=postdict, auth=auth, params=query)
            prepped = self.session.prepare_request(req)
            response = self.session.send(prepped, timeout=timeout)
            #ratelimit_remaining = response.headers['X-RateLimit-Remaining']
            #log_info(self.logger, "CURL request: {}, X-RateLimit-Remaining={}".format(url, ratelimit_remaining), True)
            # Make non-200s throw
            response.raise_for_status()

        except requests.exceptions.HTTPError as e:
            if response is None:
                raise e

            # 401 - Auth error. This is fatal.
            if response.status_code == 401:
                self.logger.error("API Key or Secret incorrect, please check and restart.")
                self.logger.error("Error: " + response.text)
                if postdict:
                    self.logger.error(postdict)
                # Always exit, even if rethrow_errors, because this is fatal
                exit(settings.FORCE_RESTART_EXIT_STATUS_CODE)

            # 404, can be thrown if order canceled or does not exist.
            elif response.status_code == 404:
                if verb == 'DELETE':
                    self.logger.error("Order not found: %s" % postdict['orderID'])
                    return
                self.logger.error("Unable to contact the BitMEX API (404). " +
                                  "Request: %s \n %s" % (url, json.dumps(postdict)))
                exit_or_throw(e)

            # 429, ratelimit; cancel orders & wait until X-RateLimit-Reset
            elif response.status_code == 429:
                self.logger.error("Ratelimited on current request. Sleeping, then trying again. Try fewer " +
                                  "order pairs or contact support@bitmex.com to raise your limits. " +
                                  "Request: %s \n %s" % (url, json.dumps(postdict)))

                # Figure out how long we need to wait.
                ratelimit_reset = response.headers['X-RateLimit-Reset']
                to_sleep = int(ratelimit_reset) - int(time.time())
                reset_str = datetime.datetime.fromtimestamp(int(ratelimit_reset)).strftime('%X')

                # We're ratelimited, and we may be waiting for a long time. Cancel orders.
                self.logger.warning("Cancelling all known orders in the meantime.")
                self.cancel([o['orderID'] for o in self.open_orders()])

                self.logger.error("Your ratelimit will reset at %s. Sleeping for %d seconds." % (reset_str, to_sleep))
                time.sleep(to_sleep)

                # Retry the request.
                return retry()

            # 503 - BitMEX temporary downtime, likely due to a deploy. Try again
            elif response.status_code == 503:
                self.logger.warning("Unable to contact the BitMEX API (503), retrying. " +
                                    "Request: %s \n %s" % (url, json.dumps(postdict)))
                time.sleep(3)
                return retry()

            elif response.status_code == 400:
                error = response.json()['error']
                message = error['message'].lower() if error else ''

                # Duplicate clOrdID: that's fine, probably a deploy, go get the order(s) and return it
                if 'duplicate clordid' in message:
                    orders = postdict['orders'] if 'orders' in postdict else postdict

                    IDs = json.dumps({'clOrdID': [order['clOrdID'] for order in orders]})
                    orderResults = self._curl_bitmex('order', query={'filter': IDs}, verb='GET')

                    for i, order in enumerate(orderResults):
                        if (
                                order['orderQty'] != abs(postdict['orderQty']) or
                                order['side'] != ('Buy' if postdict['orderQty'] > 0 else 'Sell') or
                                order['price'] != postdict['price'] or
                                order['symbol'] != postdict['symbol']):
                            raise Exception('Attempted to recover from duplicate clOrdID, but order returned from API ' +
                                            'did not match POST.\nPOST data: %s\nReturned order: %s' % (
                                                json.dumps(orders[i]), json.dumps(order)))
                    # All good
                    return orderResults

                elif 'insufficient available balance' in message:
                    self.logger.error('Account out of funds. The message: %s' % error['message'])
                    exit_or_throw(Exception('Insufficient Funds'))


            # If we haven't returned or re-raised yet, we get here.
            self.logger.error("Unhandled Error: %s: %s" % (e, response.text))
            self.logger.error("Endpoint was: %s %s: %s" % (verb, path, json.dumps(postdict)))
            exit_or_throw(e)

        except requests.exceptions.Timeout as e:
            # Timeout, re-run this request
            self.logger.warning("Timed out on request: %s (%s), retrying..." % (path, json.dumps(postdict or '')))
            return retry()

        except requests.exceptions.ConnectionError as e:
            self.logger.warning("Unable to contact the BitMEX API (%s). Please check the URL. Retrying. " +
                                "Request: %s %s \n %s" % (e, url, json.dumps(postdict)))
            time.sleep(1)
            return retry()

        # Reset retry counter on success
        self.retries = 0

        return response.json()
