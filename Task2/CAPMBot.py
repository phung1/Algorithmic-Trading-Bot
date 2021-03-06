"""
Project Name: CAPMBot
Subject Code and Name: FNCE30010 Algorithmic Trading
Student Name (ID): Zhuoqun Huang (908525)
                   Nikolai Price (836389)
                   Lee Jun Da    (888086)
"""

from enum import Enum
from fmclient import Agent, OrderSide, Order, OrderType
from fmclient.utils.constants import LOCAL_TIMEZONE, DATE_FORMAT
from typing import List, Tuple, Dict, Union
import pytz
import random
import copy
import time
import datetime

# <For debugging only>
import inspect
INIT_STACK = 12
STACK_DIF = 10
BASE_LEN = 79
DEBUG_TOGGLE = 1
# </For debugging only>

# Group details
GROUP_MEMBERS = {"908525": "Zhuoqun Huang",
                 "836389": "Nikolai Price",
                 "888086": "Lee Jun Da"}


# Market maker or reactive order
class OrderRole(Enum):
    MARKET_MAKER = 0
    REACTIVE = 1


# Dictionary to store letters in representation of a certain OrderType
# and OrderSide for reference of orders
ORDER_TYPE_TO_CHAR = {
    OrderType.LIMIT: "L",
    OrderType.CANCEL: "M"
}
ORDER_SIDE_TO_CHAR = {
    OrderSide.BUY: "B",
    OrderSide.SELL: "S"
}
ORDER_ROLE_TO_CHAR = {
    OrderRole.MARKET_MAKER: "MM",
    OrderRole.REACTIVE: "RE"
}
SEPARATION = "-"  # for most string separation


# Status of current order if there is any
class OrderStatus(Enum):
    CANCEL = -1        # Cancelled, turns INACTIVE when accepted
    INACTIVE = 0       # None/Completed/Rejected
    MADE = 1           # Made, turns PENDING when sent
    PENDING = 2        # Waiting to be accepted in the order book
    ACCEPTED = 3       # Accepted in the order book


# Status of current order if there is any
class OrderCompare(Enum):
    IDENTICAL = 0
    SAME_ORDER = 1
    SAME_PRICE = 2
    DIFFERENT = -1


# ----- Start of Helper classes -----
DATE_FORMAT = DATE_FORMAT[:-3]


def to_dollar(cents):
    return cents / 100


def to_cents(dollar):
    return dollar * 100


class Market:
    """
    Holding market state and all corresponding information of a market,
    as well as the current order status of that market
    """
    SYNC_MAX_DELAY = 2
    states = -1

    def __init__(self, market_dict: dict, agent):
        # Parse market information into the object
        self._agent: CAPMBot = agent
        self._market_id = market_dict["id"]
        self._minimum = market_dict["minimum"]
        self._maximum = market_dict["maximum"]
        self._tick = market_dict["tick"]
        self._name = market_dict["name"]
        self._item = market_dict["item"]
        self._description = market_dict["description"]
        self._payoffs = tuple(to_dollar(int(a)) for a in
                              self._description.split(","))
        if self.states == -1:
            self.set_states(len(self._payoffs))
        else:
            assert len(self._payoffs) == self.states
        self._expected_return = sum(self._payoffs) / self.states
        # Setting up own order information
        self.order_holder = OrderHolder(self._market_id, agent)
        self._current_order: MyOrder = None

        # Setting up order book
        self._order_book = []
        self._best_bids = []
        self._best_asks = []

        # Setting up holding information
        self._sync_delay = 0
        self._units = 0
        self._available_units = self._units
        # update to agent regarding holdings that is made/cancelled
        self._agent._current_holdings[self._market_id] = self._available_units
        # A virtual holding, simulating condition as if order got accepted
        self._virtual_available_units = -1

        # Record where the completed order has been read to
        self._completed_order_index = 0

    @property
    def market_id(self):
        return self._market_id

    @property
    def minimum(self):
        return self._minimum

    @property
    def maximum(self):
        return self._maximum

    @property
    def tick(self):
        return self._tick

    @property
    def name(self):
        return self._name

    @property
    def item(self):
        return self._item

    @property
    def description(self):
        return self._description

    @property
    def payoffs(self):
        return self._payoffs

    @property
    def expected_return(self):
        return self._expected_return

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, units):
        self._units = units

    @property
    def available_units(self):
        return self._available_units

    @available_units.setter
    def available_units(self, available_units):
        self._available_units = available_units

    @property
    def virtual_available_units(self):
        return self._virtual_available_units

    @virtual_available_units.setter
    def virtual_available_units(self, virtual_available_units):
        self._virtual_available_units = virtual_available_units

    @property
    def best_bids(self):
        return self._best_bids

    @property
    def best_asks(self):
        return self._best_asks

    @property
    def order_book(self):
        return self._order_book

    def update_units(self, unit_dict):
        """
        ---- Should not be used elsewhere. Need not to read ----
        This function is used in received holdings to update the units in
        one particular market. Will also keep track and update outdated
        virtual holdings.
        :param unit_dict: Standard dictionary containing the units to check
        :return:
        """

        self._agent._fn_start()
        try:
            assert (unit_dict["units"] >= 0 and
                    unit_dict["available_units"] >= 0), "negative_units"
            self._units = unit_dict["units"]
            self._available_units = unit_dict["available_units"]
            if self._virtual_available_units == -1:
                self._virtual_available_units = self._available_units
            elif self._available_units > self._virtual_available_units:
                self._sync_delay += 1
                if self._sync_delay >= self.SYNC_MAX_DELAY:
                    self._agent.inform("Market" + str(self._market_id) +
                                       " re-syncing")
                    # self.examine_units()
                    self._virtual_available_units = self._available_units
                    self._sync_delay = 0
            elif self._available_units == self._virtual_available_units:
                self._sync_delay = 0
            else:
                self._agent.error("Market" + str(self._market_id) +
                                  " Virtual Unit MORE Than available units")
                self._virtual_available_units = self._available_units
                self._sync_delay = 0
        except Exception as e:
            self._agent._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._agent._fn_end()

    @classmethod
    def set_states(cls, states):
        """
        ---- Should not be used elsewhere. Need not to read ----
        :param states:
        :return:
        """
        assert states > 0
        cls.states = states

    def order_accepted(self, order: Order):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Market side order accepted processing, update available units
        :param order: The order accepted
        """
        # self.examine_units()
        if order.side == OrderSide.SELL:
            if order.type == OrderType.LIMIT:
                self._available_units -= order.units
            else:
                self._available_units += order.units
        # self.examine_units()
        self.order_holder.order_accepted(order)

    def order_rejected(self, order: Order):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Market side order rejected processing
        :param order: The order rejected
        """
        # Only care about units if sell side (buy side in cash part)
        if order.side == OrderSide.SELL:
            if order.type == OrderType.LIMIT:
                self._virtual_available_units += order.units
            else:
                self._virtual_available_units -= order.units
        # self.examine_units()
        self.order_holder.order_rejected(order)

    def _set_bid_ask_price(self):
        """
        Update market best bid and ask based on order book holding
        """
        # Sorted from most to least to determine Best Bid
        buy_orders = sorted([order for order in self._order_book
                             if order.side == OrderSide.BUY],
                            key=self.price_key, reverse=True)
        self._best_bids = buy_orders
        if len(buy_orders) == 0:
            self._best_bids = []
        else:
            bid_price = buy_orders[0].price
            self._best_bids = [order for order in buy_orders if order.price == bid_price]

        # Sorted from lease to most to determine Best Ask
        sell_orders = sorted([order for order in self._order_book
                              if order.side == OrderSide.SELL],
                             key=self.price_key)
        if len(sell_orders) == 0:
            self._best_asks = []
        else:
            ask_price = sell_orders[0].price
            self._best_asks = [order for order in sell_orders if order.price == ask_price]

    @staticmethod
    def price_key(order):
        return order.price

    def update_received_order_book(self, order_book):
        """
        ---- Should not be used elsewhere. Need not to read ----
        :param order_book: Order book from market
        """
        try:
            self._agent._fn_start()
            self._order_book = order_book
            self._set_bid_ask_price()
            self.order_holder.update_received_order_book(order_book)
        except Exception as e:
            self._agent._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._agent._fn_end()

    def update_completed_orders(self, orders):
        """
        ---- Should not be used elsewhere. Need not to read ----
        """
        # TODO More logic here
        self.order_holder. \
            update_completed_orders(orders[self._completed_order_index:])
        self._agent.inform([order for order in
                            orders[self._completed_order_index:]
                            if order.mine and order.date >
                            self._agent._start_time])
        self._completed_order_index = len(orders)

    def add_order(self, price, units, order_type, order_side, market_id,
                  order_role):
        """
        :return: No return value
        """
        if (self._current_order is not None and
                self._current_order.order_status == OrderStatus.INACTIVE):
            self._agent.warning("Current order not sent, then added order")
            self.order_holder.remove_order(self._current_order)
        order = self.order_holder.add_order(price, units, order_type,
                                            order_side, market_id, order_role)
        self._current_order = order

    def send_current_order(self):
        """
        Send the last added order (Limit order)
        :return: Return True if successful, False Otherwise
        """
        # self.examine_units()
        if self._current_order is not None:
            # When selling, reduce virtual units
            if self._current_order.order.side == OrderSide.SELL:
                if (self._virtual_available_units <
                        self._current_order.order.units):
                    return False
                else:
                    self._virtual_available_units -= \
                        self._current_order.order.units
            self._agent.inform(self._current_order.order)
            # self.examine_units()
            return self._current_order.send()
        return False

    def cancel_order(self, order):
        my_order = self.order_holder.get_order(order)
        if my_order is None:
            return False
        # Sent the cancel order
        if my_order.cancel():
            if my_order.order.side == OrderSide.SELL:
                self._virtual_available_units += my_order.order.units
            return True
        # Didn't sent
        else:
            return False

    def is_valid_price(self, price: int) -> bool:
        """
        Check if price is valid, that is, it's proper considering minimum,
        minimum and tick
        :param price: The price to be checked
        :return: True if valid, else False
        """
        return (self._minimum < price < self.maximum and
                (price - self._minimum) % self._tick == 0)

    def examine_units(self):
        self._agent.inform("Market Id: " + str(self._market_id))
        self._agent.inform("Total units: " + str(self._units))
        self._agent.inform("Available units: " + str(self._available_units))
        self._agent.inform("Virtual available units: " +
                           str(self._virtual_available_units))


class OrderHolder:
    def __init__(self, market_id, agent):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Initialise an OrderHolder instance that holds all orders of a market
        :param market_id: Order of a market it's holding
        :param agent: The agent bot for logging
        """
        self._market_id = market_id
        self._agent: CAPMBot = agent
        # The order it's currently holding
        self._orders: List[MyOrder] = []

    @property
    def orders(self):
        """
        Retrieve active orders
        :return: list of active orders, None if failed to retrieve
        """
        try:
            return copy.deepcopy(self._orders)
        except KeyError:
            return None

    def add_order(self, price, units, order_type,
                  order_side, market_id, order_role,
                  order_status=OrderStatus.INACTIVE, orig_order=None):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Add order to order holder, default to inactive order, and return
        reference to the created MyOrder object
        :keyword order_status OrderStatus of order added (only aimed
                                                          for internal use)
        :keyword orig_order The order object to be added if
                 there is already such orders (so fixing issues)
        :return: MyOrder object created during addition
        """
        order = MyOrder(price, units, order_type, order_side,
                        market_id, order_role, order_status)
        if orig_order:
            order.order = orig_order
        self._orders.append(order)
        self._agent.inform("added order: %s" % str(order.order))
        # self._agent.inform([order.order for order in self._orders])
        # self._agent.inform([order.cancel_order for order in self._orders])
        return order

    def get_order(self, order):
        """
        Get an order based on passed order, the passed order can be either
        Order or MyOrder object, will return MyOrder object for further
        processing. WILL only get identical orders (same id or ref)
        :param order: The order to get
        :return: MyOrder if found, else None
        """
        for my_order in self._orders:
            if (MyOrder.compare_order(my_order, order)
                    == OrderCompare.IDENTICAL):
                return my_order
        else:
            return None

    def remove_order(self, order):
        """
        Remove the first appearance of given order, order can be either Order
        or MyOrder object. Will return the removed MyOrder object if successful
        :param order: The order to be removed
        :return: Corresponding MyOrder object if found, else None
        """
        my_order = self.get_order(order)
        if my_order:
            self._orders.remove(my_order)
            self._agent.inform("remove order: %s" % my_order.order)
        return my_order

    def order_accepted(self, order: Order):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Add new accepted_order to active_order
        :param order: The order accepted
        :return: True if added successfully, False otherwise
                 (E.g. Order invalid or no id for order provided)
        """
        self._agent._fn_start()
        try:
            # Check all orders to find corresponding order, and accept it
            my_order = self.get_order(order)

            if order.type == OrderType.CANCEL:
                self._agent.inform("cancel order accepted: %s" % order)
                self._agent.inform([order.order for order in self._orders])

            if my_order is not None:
                if order.type == OrderType.CANCEL:
                    self._agent.inform("cancel order found: %s" % order)
                    self._agent.inform("remove order: %s" % my_order.order)
                    self._orders.remove(my_order)
                else:
                    my_order.accepted(order)
            # Didn't find matching order
            # Don't care if it's CANCEL order
            elif order.type == OrderType.CANCEL:
                return
            # Update it to Holdings if it's not CANCEL
            else:
                self._agent.warning(str(order) + ": Didn't find "
                                                 "matching order")
                self._agent.inform([order.order for order in self._orders])
                order_role = OrderRole.REACTIVE
                if order.ref is not None:
                    if order.ref[-2:] == "MM":
                        order_role = OrderRole.MARKET_MAKER
                    else:
                        order_role = OrderRole.REACTIVE
                self.add_order(order.price, order.units, order.type,
                               order.side, order.market_id, order_role,
                               OrderStatus.ACCEPTED, order)
        except Exception as e:
            self._agent._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._agent._fn_end()

    def order_rejected(self, order: Order):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Handles rejection of orders in order holder
        :param order: The rejected order
        """
        my_order = self.get_order(order)
        # Don't care if un-recorded limit order got rejected
        if my_order is None:
            self._agent.warning(str(order) + ": Didn't find matching order")
            self._agent.inform([order.order for order in self._orders])
        else:
            if order.type == OrderType.CANCEL:
                my_order.order_status = OrderStatus.ACCEPTED
            else:
                self._agent.inform("remove order: %s" % my_order.order)
                self._orders.remove(my_order)

    def update_received_order_book(self, order_book):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Update orders based on received order book
        """
        mine_orders = [order for order in order_book if order.mine is True]
        self._orders = sort_order_by_date(self._orders)
        for order in mine_orders:
            my_order = self.get_order(order)
            # Identical order, update its delay indicator
            # Partially traded orders will be updated by completed orders
            # Don't need to update it here
            if my_order is not None:
                if my_order.order_status != OrderStatus.ACCEPTED:
                    self._agent.warning(str(order) + " state "
                                        + str(my_order.order_status))
                    my_order.order_status = OrderStatus.ACCEPTED
                if my_order.delayed():
                    my_order.cancel()
            # Didn't find order in all kept orders
            else:
                self._agent.warning(str(order) +
                                    ": Didn't find matching order")
                self._agent.inform([order.order for order in self._orders])
                # Treat it as if it's reactive order if didn't find record
                self.add_order(order.price, order.units, order.type,
                               order.side, order.market_id, OrderRole.REACTIVE,
                               OrderStatus.ACCEPTED, order)

    def update_completed_orders(self, orders):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Update orders based on completed orders
        """
        mine_orders = sort_order_by_date([order for order in
                                          orders if order.mine is True
                                          and order.date >
                                          self._agent._start_time])
        self._orders = sort_order_by_date(self._orders)
        for order in mine_orders:
            for my_order in self._orders:
                compare = MyOrder.compare_order(my_order, order)
                # Identical order, fully traded, remove it
                if compare == OrderCompare.SAME_ORDER:
                    self._agent.inform("remove order: %s" % my_order.order)
                    self._orders.remove(my_order)
                    break
                # Partially traded order
                elif compare == OrderCompare.SAME_PRICE and \
                        my_order.order.units > order.units:
                    if my_order.partial_traded(order):
                        my_order.cancel()


class MyOrder:
    MM_ORDER_MAX_DELAY = 5
    REACTIVE_ORDER_MAX_DELAY = 1
    AGENT = None

    def __init__(self, price, units, order_type, order_side, market_id,
                 order_role, order_status=OrderStatus.INACTIVE):
        ref = self._make_order_ref(price, units, order_type, order_side,
                                   market_id, order_role)
        self._order = Order(price, units, order_type, order_side, market_id,
                            ref=ref)
        self._cancel_order = None
        self._order_delay = 0
        self._order_role = order_role
        self._order_status = order_status

    @staticmethod
    def _make_order_ref(price, units, order_type,
                        order_side, market_id, order_role):
        """
        Make the standard reference for an order
        :param price: price the order is placed on
        :param units: units the order is trading
        :param order_type: Limit or Cancel
        :param order_side: Buy or sell
        :param market_id:  Market order is trading on
        :param order_role: Market maker or reactive
        :return: A standard string containing all information
        """
        ref = ":" + time.strftime(DATE_FORMAT, time.localtime()) + SEPARATION
        ref += str(price) + SEPARATION
        ref += str(units) + SEPARATION
        ref += ORDER_TYPE_TO_CHAR[order_type] + SEPARATION
        ref += ORDER_SIDE_TO_CHAR[order_side] + SEPARATION
        ref += str(market_id) + SEPARATION
        ref += ORDER_ROLE_TO_CHAR[order_role]
        return ref

    @property
    def order(self):
        return copy.copy(self._order)

    @order.setter
    def order(self, order):
        if isinstance(order, Order):
            self._order = order

    @property
    def cancel_order(self):
        return copy.copy(self._cancel_order)

    @cancel_order.setter
    def cancel_order(self, cancel_order):
        if isinstance(cancel_order, Order):
            self._cancel_order = cancel_order

    @property
    def order_status(self):
        return self._order_status

    @order_status.setter
    def order_status(self, status):
        if isinstance(status, OrderStatus):
            self._order_status = status

    def send(self):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Send the order
        :return: True if successfully sent, False otherwise
        """
        self.AGENT.inform(self)
        if self.AGENT is not None and self._order_status == \
                OrderStatus.INACTIVE:
            self.AGENT.send_order(self._order)
            self._order_status = OrderStatus.PENDING
            return True
        return False

    def cancel(self):
        """
        Cancel this order
        :return: True if cancel success, False otherwise
        """
        if self.AGENT is not None and self._order_status == \
                OrderStatus.ACCEPTED:
            self._cancel_order = copy.copy(self._order)
            self._cancel_order.type = OrderType.CANCEL
            self._cancel_order.ref = \
                self._make_order_ref(self._cancel_order.price,
                                     self._cancel_order.units,
                                     self._cancel_order.type,
                                     self._cancel_order.side,
                                     self._cancel_order.market_id,
                                     self._order_role)
            self.AGENT.send_order(self._cancel_order)
            return True
        return False

    def accepted(self, order):
        self._order = order
        self._order_status = OrderStatus.ACCEPTED

    def delayed(self, times=1):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Called when this order is delayed,
        and return if the order should be cancelled
        :return: True if exceeded max delay, false otherwise
        """
        self._order_delay += times
        return self._should_cancel()

    def partial_traded(self, order):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Called when order is partially_traded and return if the order
        should be cancelled, if not reset the order delay
        :param order: The order in completed orders
        """
        self.AGENT.inform("partial traded order: %s" % order)
        if self._order_role == OrderRole.REACTIVE:
            return True
        self._order_delay = 0
        self._order.units = self._order.units - order.units
        self._order = order
        return False

    def _should_cancel(self):
        """
        Check if should cancel itself, based on the current delay
        :return: True if need to cancel, False otherwise
        """
        if self._order_role == OrderRole.MARKET_MAKER and \
                self._order_delay >= self.MM_ORDER_MAX_DELAY:
            self.AGENT.inform("mm delay = %d " % self._order_delay)
            return True
        elif self._order_role == OrderRole.REACTIVE and \
                self._order_delay >= self.REACTIVE_ORDER_MAX_DELAY:
            self.AGENT.inform("reactive delay = %d " % self._order_delay)
            return True
        else:
            return False

    @classmethod
    def set_agent(cls, agent):
        """
        Set up the agent used to send orders
        """
        cls.AGENT: CAPMBot = agent

    @staticmethod
    def compare_order(order1: Union[Order, "MyOrder"],
                      order2: Union[Order, "MyOrder"]):
        """
        ---- Should not be used elsewhere. Need not to read ----
        Compare if two orders are same, either Order or MyOrder can be passed
        :param order1: The first order to compare
        :param order2: The first order to compare
        :return: OrderCompare.IDENTICAL if two orders are completely same
                 OrderCompare.SAME_PRICE if two orders differs in unit but
                                    not price (thus might be the same order)
                 OrderCompare.DIFFERENT if two orders can't possibly be same
        """
        # Check if want to match cancel orders
        limit = True
        if ((isinstance(order1, Order) and order1.type == OrderType.CANCEL) or
                (isinstance(order2, Order) and
                 order2.type == OrderType.CANCEL)):
            limit = False
        if isinstance(order1, MyOrder):
            order1 = order1._order if limit else order1._cancel_order
        if isinstance(order2, MyOrder):
            order2 = order2._order if limit else order2._cancel_order
        # When side or type different
        if order1 is None or order2 is None or\
                order1.side != order2.side or order1.type != order2.type:
            return OrderCompare.DIFFERENT
        # Handles accepted_order() and rejected order()
        elif (order1.ref is not None and order2.ref is not None
              and order1.ref == order2.ref):
            return OrderCompare.IDENTICAL
        # Handles accepted order but not traded yet
        elif (order1.id is not None and order2.id is not None and
              order1.id == order2.id):
            return OrderCompare.IDENTICAL
        # When price same but units differs
        elif order1.price == order2.price and order1.units == order2.units:
            return OrderCompare.SAME_ORDER
        elif order1.price == order2.price and order1.units != order2.units:
            return OrderCompare.SAME_PRICE
        # Other conditions
        else:
            return OrderCompare.DIFFERENT


def key(order):
    """
    ---- Should not be used elsewhere. Need not to read ----
    Takes an Order or a MyOrder object and return it's date attribute, for
    sorting purpose only. For order WITHOUT date, the time now will
    be used
    :param order: the object to be sorted
    :return: datetime object contained in it
    """
    assert isinstance(order, (MyOrder, Order))
    if isinstance(order, MyOrder):
        date = order.order.date
    else:
        date = order.date
    if date is None:
        date = datetime.datetime.now(tz=pytz.timezone(LOCAL_TIMEZONE))
    return date


def sort_order_by_date(orders, reverse=False):
    """
    ---- Should not be used elsewhere. Need not to read ----
    Sort the given orders by time so that comparing logic will work well.
    :return: Sorted list or orders
    """
    return sorted(orders, key=key, reverse=reverse)

# ----- End of Helper classes -----


class CAPMBot(Agent):
    MAX_SYNC_DELAY = 2

    def __init__(self, account, email, password, marketplace_id,
                 risk_penalty=0.01, session_time=20):
        """
        Constructor for the Bot
        :param account: Account name
        :param email: Email id
        :param password: password
        :param marketplace_id: id of the marketplace
        :param risk_penalty: Penalty for risk
        :param session_time: Total trading time for one session
        """
        super().__init__(account, email, password, marketplace_id,
                         name="CAPM_Bot")
        self._session_time = session_time
        self._start_time = datetime.datetime.\
            now(tz=pytz.timezone(LOCAL_TIMEZONE))
        self._to_change_behaviour = \
            datetime.datetime.now(tz=pytz.timezone(LOCAL_TIMEZONE)) + \
            datetime.timedelta(minutes=(session_time-2))
        self._risk_penalty = risk_penalty
        self._my_markets: Dict[int, Market] = {}
        self._market_ids = {}
        self._covariances = {}
        self._variances = {}
        self._current_holdings = {}
        self._note_id = -1

        self._cash = 0
        self._available_cash = self._cash
        # A virtual holding, simulating condition if order got accepted
        self._sync_delay = -1
        self._virtual_available_cash = self._available_cash

        # Set up agent for Order sender
        MyOrder.set_agent(self)

    def initialised(self):
        """
        Initialise by looking at the requirements of the market,
        collects data regarding the market to be traded in and their
        respective payoff
        """
        self._fn_start()
        for market_id, market_dict in self.markets.items():
            self.inform(market_id)
            self.inform(self._str_market(market_dict))
            self._my_markets[market_id] = Market(market_dict, self)
            self._market_ids[self._my_markets[market_id].item] = market_id
        self.inform(self._market_ids)
        self._build_variance()
        self._build_covariance()
        self.inform("There are %s possible states" % str(Market.states))
        # Note market
        for market in self._market_ids.values():
            if self._variances[market] == 0:
                self._note_id = market
        self._fn_end()

    def get_potential_performance(self, orders=None):
        """
        Returns the portfolio performance if the given list of orders is
        executed.

        :param orders: list of orders (list of lists)
        :return: performance
        """
        new_cash = self._cash
        holdings = {}
        for market_id in self._my_markets.keys():
            holdings[market_id] = \
                self._my_markets[market_id].units
        if isinstance(orders, Order):
            orders = [orders]
        elif orders is None:
            orders = []
        for order in orders:
            if order.side == OrderSide.SELL:
                holdings[order.market_id] -= order.units
                new_cash += order.price * order.units
            else:
                holdings[order.market_id] += order.units
                new_cash -= order.price * order.units
        performance = self._calculate_performance(new_cash, holdings)
        return performance

    def _note_orders(self, market_id):
        """
        Order management for the note market
        :param market_id:
        :return: list of order option that is viable
        """
        self._fn_start()
        try:
            notes_units = self._my_markets[market_id].available_units
            if self._my_markets[market_id]._best_bids and notes_units > 0:
                # Best bid in notes market
                best_bid_price = self._my_markets[market_id]._best_bids[0].price
                # Sell notes for more than their expected return
                if best_bid_price >= self._my_markets[market_id].expected_return:
                    self._send_order(best_bid_price, 1, OrderType.LIMIT,
                                     OrderSide.SELL, market_id, OrderRole.REACTIVE)
                # Check each market for whether buying is profitable
                for other_market_id in self._market_ids.values():
                    if self._my_markets[other_market_id]._best_bids and notes_units > 0:
                        # Best bid in the market
                        market_best_bid = self._my_markets[other_market_id]._best_bids[0].price
                        if self._available_cash < market_best_bid:
                            sell_note = Order(best_bid_price, 1, OrderType.LIMIT, OrderSide.SELL,
                                              market_id)
                            buy_sec = Order(market_best_bid, 1, OrderType.LIMIT, OrderSide.BUY,
                                            other_market_id)
                            # Check if selling note and buying sec will increase performance
                            if self.get_potential_performance([sell_note, buy_sec]) > \
                                    self.get_potential_performance():
                                self._send_order(best_bid_price, 1, OrderType.LIMIT, OrderSide.SELL,
                                                 market_id, OrderRole.REACTIVE)
                    else:
                        self.\
                            _send_order(
                                        to_cents(self._my_markets
                                                 [market_id].payoffs[0]),
                                        1, OrderType.LIMIT, OrderSide.SELL,
                                        market_id, OrderRole.MARKET_MAKER)
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def _process_order(self, market_id):
        """
        Process all order before passing through get_potential_performance
        and finally send order
        :return: Order Made -> bool
        """
        self._fn_start()
        try:
            for market in self._market_ids.values():
                self._current_holdings[market] = \
                    self._my_markets[market].units
            current_performance = self. \
                _calculate_performance(self._cash,
                                       self._current_holdings)
            self.inform("current_performance=%.3f" % current_performance)
            # Logic for notes
            if market_id == self._note_id:
                self._note_orders(market_id)
            # Logic for other secs
            else:
                # Find sell performance improving sell orders
                bid_side = self._my_markets[market_id].best_bids
                ask_side = self._my_markets[market_id].best_asks
                orders = []
                orders += self._compute_reactive_orders(bid_side, market_id)
                orders += self._compute_reactive_orders(ask_side, market_id)
                self.inform(orders)
                orders = sorted(orders, key=lambda x: x[1], reverse=True)
                if len(orders) > 0 and orders[0][1] > current_performance:
                    return self._send_order(orders[0][0].price,
                                            orders[0][0].units,
                                            orders[0][0].type,
                                            orders[0][0].side,
                                            orders[0][0].market_id,
                                            OrderRole.REACTIVE)

                orders = self._compute_mm_orders(market_id,
                                                 current_performance)
                if len(bid_side) > 0 and len(ask_side) > 0 \
                        and datetime.\
                        datetime.now(tz=pytz.timezone(LOCAL_TIMEZONE)) \
                        > self._to_change_behaviour:
                    # TODO change behaviour of bot when time is almost ending
                    orders += self._creep_bid_ask_spread(bid_side, ask_side,
                                                         market_id)
                orders = sorted(orders, key=lambda x: x[1], reverse=True)
                if len(orders) > 0 and orders[0][1] > current_performance:
                    return self._send_order(orders[0][0].price,
                                            orders[0][0].units,
                                            orders[0][0].type,
                                            orders[0][0].side,
                                            orders[0][0].market_id,
                                            OrderRole.MARKET_MAKER)
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def _compute_reactive_orders(self, other_orders, market_id,
                                 check_order=True):
        self._fn_start()
        try:
            orders = []
            other_orders = [order for order in other_orders
                            if order.mine is False]
            if len(other_orders) > 0:
                price = other_orders[0].price
                side = (OrderSide.BUY if other_orders[0].side ==
                        OrderSide.SELL else OrderSide.SELL)
                total_units = sum([order.units for order in other_orders])
                for units in range(1, total_units + 1):
                    order = Order(price, units, OrderType.LIMIT, side,
                                  market_id)
                    performance = self.get_potential_performance([order])
                    if check_order is False:
                        orders.append([order, performance])
                    else:
                        if self._check_order(price, units, side, market_id):
                            orders.append([order, performance])

            return orders
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def _compute_mm_orders(self, market_id, baseline_performance,
                           check_order=True):
        """
        Create prices that may be profitable
        :param market_id: market to be traded in
        :param baseline_performance: order side
        :param check_order: should order be checked validity
        :return performance with order
        """
        try:
            orders = []
            tick = self._my_markets[market_id].tick
            minimum = self._my_markets[market_id].minimum
            maximum = self._my_markets[market_id].maximum
            best_bids = self._my_markets[market_id].best_bids
            best_bid_price = best_bids[0].price if \
                len(best_bids) > 0 else minimum
            best_asks = self._my_markets[market_id].best_asks
            best_ask_price = best_asks[0].price if \
                len(best_asks) > 0 else maximum
            spread = best_ask_price - best_bid_price
            min_over_tick = (random.randint(1, spread // 3) + tick) // tick

            for price in range(best_bid_price + tick, best_ask_price, tick):
                if price <= best_ask_price - min_over_tick * tick:
                    order = Order(price, 1, OrderType.LIMIT, OrderSide.SELL,
                                  market_id)
                    if check_order is False or \
                            self._check_order(price, 1,
                                              OrderSide.SELL, market_id):
                        performance = self.get_potential_performance([order])
                        if performance > baseline_performance:
                            orders.append([order, performance])
                if price >= best_bid_price + min_over_tick * tick:
                    order = Order(price, 1, OrderType.LIMIT, OrderSide.BUY,
                                  market_id)
                    if check_order is False or \
                            self._check_order(price, 1,
                                              OrderSide.BUY, market_id):
                        performance = self.get_potential_performance([order])
                        if performance > baseline_performance:
                            orders.append([order, performance])
            return orders

        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])

    def _creep_bid_ask_spread(self, bid, ask, market_id, check_order=True):
        """
        Creep between the bid ask spread (to be called when approaching 20 mins)
        But only if spread is bigger than 3 ticks, else trade as normal
        :param market_id: Market ID
        :return: orders
        """
        best_bid_price = bid[0].price
        best_ask_price = ask[0].price
        bid_ask_spread = best_bid_price - best_ask_price
        tick = self._my_markets[market_id].tick
        orders = []
        if bid_ask_spread > 3*tick:
            bid_price = best_bid_price + tick
            for units in range(1, 5):
                if check_order is False or \
                        self._check_order(bid_price, units, OrderSide.BUY,
                                          market_id):
                    order = Order(bid_price, units, OrderType.LIMIT,
                                  OrderSide.BUY, market_id)
                    performance = self.get_potential_performance([order])
                    orders.append([order, performance])

            ask_price = best_ask_price - tick
            for units in range(1, 5):
                if check_order is False or \
                        self._check_order(ask_price, units, OrderSide.SELL,
                                          market_id):
                    order = Order(ask_price, units, OrderType.LIMIT,
                                  OrderSide.SELL, market_id)
                    performance = self.get_potential_performance([order])
                    orders.append([order, performance])

        return orders

    def _build_covariance(self) -> None:
        """
        Build the covariance for all payoffs
        :return: None, builds the covariance dictionary
        """
        for first_iter_market in self._my_markets.keys():
            market_id1 = self._my_markets[first_iter_market].market_id
            for second_iter_market in self._my_markets:
                market_id2 = self._my_markets[second_iter_market].market_id
                to_be_key = sorted([market_id1, market_id2])
                key_for_dict = str(to_be_key[0])+'-'+str(to_be_key[1])
                if market_id1 != market_id2 and \
                        key_for_dict not in self._covariances:
                    self._covariances[key_for_dict] = \
                        self._compute_covariance(
                            self._my_markets[first_iter_market].payoffs,
                            self._my_markets[second_iter_market].payoffs,
                            self._my_markets[first_iter_market].expected_return,
                            self._my_markets[second_iter_market].expected_return)
                    self.inform(self._read_covariance
                                (market_id1, market_id2,
                                 self._covariances[key_for_dict]))

    def _build_variance(self) -> None:
        """
        Build the Variance for all Payoffs
        :return: None, builds the variance dictionary
        """
        for market in self._my_markets.keys():
            self._variances[market] = \
                self._compute_variance(self._my_markets[market].payoffs)
            self.inform(self._read_variance(market, self._variances[market]))

    @staticmethod
    def _compute_variance(payoff: Tuple[float]) -> float:
        """
        Compute the variance of the market's payoff
        :param payoff: Tuple of the market's payoff
        :return: the variance value
        """
        squared_payoff = []
        for states in payoff:
            squared_payoff.append(states**2)
        return ((1/Market.states)*sum(squared_payoff)) - \
               ((1/(Market.states**2))*(sum(payoff)**2))

    @staticmethod
    def _compute_covariance(payoff1, payoff2, exp_ret1, exp_ret2):
        """
        Compute the covariance between list of payoff1 and payoff2, they
        have to be the same length
        :param payoff1: Payoff of first market
        :param payoff2: Payoff of second market
        :param exp_ret1: Expected Return of first market
        :param exp_ret2: Expected Return of second market
        :return: The covariance between the 2 market
        """
        cross_multiply = []
        for num in range(Market.states):
            cross_multiply.append(payoff1[num]*payoff2[num])
        return (1/Market.states)*sum(cross_multiply) - (exp_ret1*exp_ret2)

    def _units_payoff_variance(self, units):
        """
        Computes the payoff variance of expected and current holdings
        :param units: holdings of a certain market stock
        :return: Payoff Variance
        """
        total_variance = 0
        # Holding squared times its variance
        for market_id in units.keys():
            total_variance += (units[market_id]**2) * \
                              (self._variances[market_id])

        # Holding1 times Holding2 times covariance
        for market_ids in self._covariances.keys():
            ind_market_id = market_ids.split('-')
            total_variance += (2*units[int(ind_market_id[0])]) * \
                              (units[int(ind_market_id[1])]) * \
                              (self._covariances[market_ids])
        return total_variance

    def _calculate_performance(self, cash, holdings):
        """
        Calculates the portfolio performance
        :param cash: cash
        :param holdings: current holdings
        :return: performance
        """
        b = self._risk_penalty
        expected_payoff = to_dollar(cash)
        tot_payoff_variance = self._units_payoff_variance(holdings)
        for market in holdings.keys():
            expected_payoff += self._my_markets[market].expected_return * \
                               holdings[market]
        return expected_payoff - b*tot_payoff_variance

    def is_portfolio_optimal(self):
        """
        Returns true if the current holdings are optimal with respect to
        current market bid and ask (as per the performance formula),
        false otherwise.
        :return:
        """
        for market in self._market_ids.values():
            self._current_holdings[market] = \
                self._my_markets[market].units
        current_performance = self. \
            _calculate_performance(self._cash,
                                   self._current_holdings)

        for market in self._market_ids.values():
            if self._my_markets[market].best_bids:
                best_bid = self._my_markets[market].best_bids[0].price
                sell_order = Order(best_bid, 1, OrderType.LIMIT, OrderSide.SELL,
                                   market)
                if self.get_potential_performance([sell_order]) > \
                        current_performance:
                    return False

            if self._my_markets[market].best_asks:
                best_ask = self._my_markets[market].best_asks[0].price
                buy_order = Order(best_ask, 1, OrderType.LIMIT, OrderSide.BUY,
                                  market)
                if self.get_potential_performance([buy_order]) > \
                        current_performance:
                    return False
        return True

    def order_accepted(self, order):
        try:
            self._fn_start()
            if order is None:
                self.error("order_accepted: None Order")
            market = self._my_markets[order.market_id]
            market.order_accepted(order)

            self.inform(order)
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def order_rejected(self, info, order):
        try:
            self._fn_start()
            self.error("order rejected:" + str(info))
            # self.examine_cash()
            market = self._my_markets[order.market_id]
            market.order_rejected(order)

            self.inform(order)
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def received_order_book(self, order_book, market_id):

        self._fn_start()
        try:
            self.get_completed_orders(market_id)
            self.inform("received order book from %d" % market_id)
            elapsed_time = (datetime.datetime.now(tz=pytz.timezone(LOCAL_TIMEZONE))
                            - self._start_time)/datetime.timedelta(minutes=1)
            self.inform("time elapsed %.3f minutes" % elapsed_time)
            self._update_received_order_book(order_book, market_id)
            self._process_order(market_id)
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def received_completed_orders(self, orders, market_id=None):
        try:
            self._fn_start()
            if market_id is not None:
                self._my_markets[market_id].update_completed_orders(orders)
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def received_holdings(self, holdings):
        try:
            self._fn_start()
            cash = holdings["cash"]
            self._cash = cash["cash"]
            self._available_cash = cash["available_cash"]
            self._line_break_inform()
            # self.examine_cash()
            if self._virtual_available_cash > self._available_cash:
                self._virtual_available_cash = self._available_cash
            elif self._virtual_available_cash < self._available_cash:
                if self._sync_delay == -1 or\
                        self._sync_delay >= self.MAX_SYNC_DELAY:
                    self.inform("Virtual cash re-syncing")
                    self._virtual_available_cash = self._available_cash
                    self._sync_delay = 0
                else:
                    self._sync_delay += 1

            for market_id, units in holdings["markets"].items():
                self.inform(market_id)
                self._my_markets[market_id].update_units(units)
                self._my_markets[market_id].examine_units()
            if self.is_portfolio_optimal():
                self.inform("portfolio give market optimal")
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def received_marketplace_info(self, marketplace_info):
        self._fn_start()
        self.inform(marketplace_info)
        session_id = marketplace_info["session_id"]
        if marketplace_info["status"]:
            self.inform("Marketplace is now open with session id "
                        + str(session_id))
            self._start_time = datetime.datetime.now(tz=pytz.
                                                     timezone(LOCAL_TIMEZONE))
            self._to_change_behaviour = \
                datetime.datetime.now(tz=pytz.timezone(LOCAL_TIMEZONE)) + \
                datetime.timedelta(minutes=self._session_time-2)
        else:
            self.inform("Marketplace is now closed.")

        self._fn_end()

    # --- ORDER HANDLER section ---
    def _update_received_order_book(self, order_book: List[Order],
                                    market_id: int) -> None:
        """
        Update active order based on received order book (Don't use this)
        :param order_book: Received Order book
        :param market_id: Id of the market where order_book come from
        """

        self._my_markets[market_id].update_received_order_book(order_book)
        self._my_markets[market_id]._set_bid_ask_price()

    def _update_completed_order(self, orders: List[Order],
                                market_id: int) -> None:
        """
        Update active orders based on received completed orders (Don't use)
        :param orders: List of completed orders
        :param market_id: Id of the market where completed orders come from
        """
        self._my_markets[market_id].update_completed_orders(orders)

    def _check_order(self, price, units, order_side, market_id) -> bool:
        """
        Check if an order can be sent based on cash or unit holdings, this only
        support limit order, use MyOrder's object method cancel to cancel order
        :param price: price to send order at
        :param units: units to send
        :param order_side: side of order
        :param market_id:  id of market
        :return: True if can send, False if order is null
        """
        if order_side == OrderSide.BUY:
            return self._virtual_available_cash >= price * units
        else:
            market: Market = self._my_markets[market_id]
            return (market.is_valid_price(price) and
                    market.virtual_available_units >= units)

    def _send_order(self, price, units, order_type,
                    order_side, market_id, order_role) -> bool:
        """
        Check and send an order
        :param price: price to send order at
        :param units: units to send
        :param order_type: type of order
        :param order_side: side of order
        :param market_id:  id of market
        :param order_role: role of order (market_maker or reactive)
        :return: True if successfully sent, false if failed check
        """
        try:
            self.inform("--Sending order--")
            if self._check_order(price, units, order_side, market_id):
                market: Market = self._my_markets[market_id]

                market.add_order(price, units, order_type, order_side,
                                 market_id, order_role)
                self.inform(market._current_order.order)
                result = market.send_current_order()
                # self.examine_cash()
                self._virtual_available_cash -= (price * units if result and
                                                 order_side == OrderSide.BUY
                                                 else 0)
                return result
            else:
                return False
        except Exception as e:
            self._exception_inform(e, inspect.stack()[0][3])
        finally:
            self._fn_end()

    def _cancel_order(self, order: Order) -> bool:
        """
        cancel a given order, the order should be an Order object
        :param order: the order to be cancelled
        :return: True if successfully sent order, false otherwise
        """
        market = self._my_markets[order.market_id]
        return market.cancel_order(order)
    # ---   END ORDER HANDLER   ---

    def run(self):
        self.initialise()
        self.start()

    def _line_break_inform(self, msg="", char="-",
                           length=BASE_LEN, width=BASE_LEN):
        """
        Simply inform a line break with certain character
        :param char:   The character to be repeated
        :param length: The number of repetition char would be repeated
        :param width:  The least width of line (symmetric space padding)
        """
        if msg != "":
            msg = "  " + msg + "  "
        len_char = (length - len(msg)) // len(char)
        char_left = len_char // 2
        char_right = len_char - char_left
        len_space = width - length
        if len_space < 0:
            len_space = 0
        space_left = len_space // 2
        space_right = len_space - space_left

        self.inform(" " * space_left + "".join([char] * char_left) +
                    msg + "".join([char] * char_right) + " " * space_right)

    def _exception_inform(self, msg, fn_name,
                          addition=""):
        """
        Show the exception message with function name
        :param msg: exception to inform
        """
        assert isinstance(msg, Exception), ("msg %s is not an exception"
                                            % str(msg))
        if len(addition) > 0:
            addition = "addition, " + addition
        self.warning("^^^Exception in function %s^^^:"
                     "msg: %s%s" % (fn_name, str(msg), addition)
                     )

    def examine_cash(self):
        self.inform("Total cash: " + str(self._cash))
        self.inform("Available cash: " + str(self._available_cash))
        self.inform("Virtual available cash: " +
                    str(self._virtual_available_cash))

    @staticmethod
    def _str_market(market):
        """
        This is a staticmethod that returns the string representation of detail
        of a market
        :param market: Dictionary of a market to be turned into string
        """
        try:
            return ("Market: %d\n"
                    "                       Minimum: %3d\n"
                    "                       Maximum: %3d\n"
                    "                       Tick   : %3d\n"
                    "                       Name   : %s\n"
                    "                       Item   : %s\n"
                    "                       Describ: %s\n" %
                    (market["id"], market["minimum"], market["maximum"],
                     market["tick"], market["name"], market["item"],
                     market["description"]))
        except Exception as e:
            return e

    # Used for visualisation of function call as stacks, that it's easier to
    # trace through functions

    @staticmethod
    def _read_variance(market, variance):
        return "The variance for market %d is %3d" % (market, variance)

    @staticmethod
    def _read_covariance(market1, market2, covariance):
        return "The covariance between market %d and market %d is %3d" \
               % (market1, market2, covariance)

    @staticmethod
    def get_stack_size():
        """
        Get stack size for caller's frame.
        %timeit len(inspect.stack())
        8.86 ms ± 42.5 µs per loop (mean ± std. dev. of 7 runs, 100 loops each)
        %timeit get_stack_size()
        4.17 µs ± 11.5 ns per loop (mean ± std. dev. of 7 runs, 100000 loops each)
        """
        return len(inspect.stack())

    def _fn_start(self):
        if not DEBUG_TOGGLE == 1:
            return
        self._line_break_inform(inspect.stack()[1][3], char="v",
                                length=BASE_LEN + INIT_STACK * STACK_DIF -
                                       (self.get_stack_size()-1) * STACK_DIF)

    def _fn_end(self):
        if not DEBUG_TOGGLE == 1:
            return
        self._line_break_inform(inspect.stack()[1][3], char="^",
                                length=BASE_LEN + INIT_STACK * STACK_DIF -
                                       (self.get_stack_size()-1) * STACK_DIF)


if __name__ == "__main__":
    FM_ACCOUNT = "bullish-delight"

    FM_EMAIL_CH = "z.huang51@student.unimelb.edu.au"
    FM_PASSWORD_CH = "908525"
    FM_CH = [FM_EMAIL_CH, FM_PASSWORD_CH]

    FM_EMAIL_JD = "j.lee161@student.unimelb.edu.au"
    FM_PASSWORD_JD = "888086"
    FM_JD = [FM_EMAIL_JD, FM_PASSWORD_JD]

    FM_EMAIL_NP = "n.price3@student.unimelb.edu.au"
    FM_PASSWORD_NP = "836389"
    FM_NP = [FM_EMAIL_NP, FM_PASSWORD_NP]

    MARKETPLACE_MANUAL = 387
    MARKETPLACE_ID1 = 372   # 3 risky 1 risk-free
    MARKETPLACE_ID2 = 363   # 2 risky 1 risk-free

    FM_SETTING = [FM_ACCOUNT] + FM_JD
    FM_SETTING.append(MARKETPLACE_ID1)
    bot = CAPMBot(*FM_SETTING)
    bot.run()
