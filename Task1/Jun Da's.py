"""
This is a template for Project 1, Task 1 (Induced demand-supply)
"""

from enum import Enum
from fmclient import Agent, OrderSide, Order, OrderType
import time

# Group details
GROUP_MEMBERS = {"908525": "Zhuoqun Huang", "836389": "Nikolai Price", "888086": "Lee Jun Da"}

# ------ Add a variable called DS_REWARD_CHARGE -----
# Dependent on actual task
DS_REWARD_CHARGE = 500


# Enum for the roles of the bot
class Role(Enum):
    BUYER = 0
    SELLER = 1


# Let us define another enumeration to deal with the type of bot
class BotType(Enum):
    MARKET_MAKER = 0
    REACTIVE = 1


# Defining enumeration for the status of the bot
class BotStatus(Enum):
    UNABLE_UNITS_MAX = -1
    UNABLE_CASH_NONE = 0
    ACTIVE = 1


# Defining another enumeration for the status of orders
class OrderStatus(Enum):
    MAKING = 1
    PENDING = 2
    ACCEPTED = 3
    COMPLETED = 4
    REJECTED = -1
    CANCELLED = 0


# Dictionary to store letters in representation of a certain OrderType and OrderSide for reference of orders
ORDER_TYPE_TO_CHAR = {OrderType.LIMIT: "L", OrderType.CANCEL: "M"}
ORDER_SIDE_TO_CHAR = {OrderSide.BUY: "B", OrderSide.SELL: "S"}
SEPARATION = "-"  # for most string separation
TIME_FORMATTER = "%y" + SEPARATION + "%m" + SEPARATION + "%d" + \
                 SEPARATION + "%H" + SEPARATION + "%M" + SEPARATION + "%S"


class DSBot(Agent):

    # ------ Constructor and initialisation methods -----
    def __init__(self, account, email, password, marketplace_id):
        super().__init__(account, email, password, marketplace_id, name="DSBot")
        self._market_id = -1
        self._bot_type = BotType(0)
        # For storing all markets available
        self._all_markets = {}

        self._role = self.role()
        self._bot_status = BotStatus["ACTIVE"]

    def run(self):
        self.initialise()
        self.start()

    def initialised(self):
        for market_id, market_dict in self.markets.items():
            self._all_markets[market_id] = (MyMarkets(market_dict, self))
            self._market_id = market_id
            self.inform("Added market with id %d" % market_id)
        self.inform("Finished Adding markets, the current list of markets are: " + repr(self._all_markets))

    # ------ End of Constructor and initialisation methods -----

    def received_holdings(self, holdings):
        """
        Read current holdings of account to make sure trade is possible
        :param holdings: Holdings of the account (Cash, Available Cash, Units, Available units)
        :return: return holdings of account
        """
        cash_holdings = holdings["cash"]
        self.inform("Total cash: " + str(cash_holdings["cash"]) +
                    " available cash: " + str(cash_holdings["available_cash"]))
        for market_id, market_holding in holdings["markets"].items():
            self.inform("Market ID " + str(market_id) + ": total units: " +
                        str(market_holding["units"]) + ", available units: " + str(market_holding["available_units"]))

        if self.role == Role["SELLER"]:
            if market_holding["available_units"] == 0:
                self._bot_status = BotStatus["UNABLE_UNITS_MAX"]
                self.inform("Role-Seller: No more available units, unable to continue trade")
            else:
                self._bot_status = BotStatus["ACTIVE"]

        if self._role == Role["BUYER"]:
            if cash_holdings["available_cash"] == 0:
                self._bot_status = BotStatus["UNABLE_CASH_ZERO"]
                self.inform("Role-Buyer: No more available cash, unable to continue trade")
            elif market_holding["units"] == 5:
                self._bot_status = BotStatus["UNABLE_UNITS_MAX"]
            else:
                self._bot_status = BotStatus["ACTIVE"]


    def received_order_book(self, order_book, market_id):
        """
        Most logic should reside in this function, and it should also cooperate with other classes
        :param order_book: The order book of specific market
        :param market_id:  Id of the corresponding market
        :return: No return. Only processes to be executed.
        """
        self.inform("received order book from %d" % market_id)

        # Task spec specify bot need to be either reactive or market maker, not both
        # depending on the type of bot, make orders when appropriate.
        # When the bid-ask spread is large, print can buy at Lowest sell price or sell at highest buy price

        best_ask = None
        best_bid = None

        for order in order_book:
            if order.mine:
                MyOrder.status = OrderStatus["PENDING"]

            price = order.price
            units = order.units

            if order.side == OrderSide.SELL:
                # determine which is lowest ASK price
                if best_ask == None:
                    best_ask = (price, units)
                else:
                    if price < best_ask[0]:
                        best_ask = (price, units)

            elif order.side == OrderSide.BUY:
                # determine which is highest BID price
                if best_bid == None:
                    best_bid = (price, units)
                else:
                    if price > best_bid[0]:
                        best_bid = (price, units)

        try:
            bid_ask_spread = best_ask[0] - best_bid[0]
            self.inform("Spread is: " + str(bid_ask_spread))
        except TypeError:
            self.inform("no bid ask spread available")

        if self._bot_status == BotStatus["ACTIVE"]:
            # Bot is a buyer
            if self._role == Role["BUYER"]:
                if (self.status is None) or (self.status == OrderStatus["CANCELLED"]):
                    my_order = MyOrder(bid_price, 1, OrderType.LIMIT, OrderSide.BUY, market_id)
                    my_order.send_order(self)
                pass

            # Bot is a seller
            if self._role == Role["SELLER"]:
                if (self.status is None) or (self.status == OrderStatus["CANCELLED"]):
                    my_order = MyOrder(ask_price, 1, OrderType.LIMIT, OrderSide.BUY, market_id)
                    my_order.send_order(self)
                pass

        self.inform(MyOrder.status)
        # Create bid-ask spread and check for depth of order
        # Depending on role, choose to buy or sell at relevant price


    def _reactive_orders_price(self, best_bid, best_ask, market_id):
        """
        Because bottype is REACTIVE, make orders at best_bid and best_ask
        :param best_bid: best bid in the market right now
        :param best_ask: best ask in the market right now
        :param market_id: to be parsed in order to make order here
        :return: makes and sends order
        """

        bid_price = best_bid
        ask_price = best_ask

        # Bot is a buyer
        if self._role == Role["BUYER"]:
            if (MyOrder.status is None) or (MyOrder.status == OrderStatus["CANCELLED"]):
                my_order = MyOrder(bid_price, 1, OrderType.LIMIT, OrderSide.BUY, market_id)
                my_order.send_order(self)

        # Bot is a seller
        if self._role == Role["SELLER"]:
            if (MyOrder.status is None) or (MyOrder.status == OrderStatus["CANCELLED"]):
                my_order = MyOrder(ask_price, 1, OrderType.LIMIT, OrderSide.BUY, market_id)
                my_order.send_order(self)

    def received_marketplace_info(self, marketplace_info):
        pass

    def received_completed_orders(self, orders, market_id=None):
        for order in orders:
            if order.mine:
                MyOrder.status = OrderStatus["COMPLETE"]
                self.inform("Order with ref " + order.ref + "was completed in market" + str(market_id))
        pass

    # ------ Helper and trivial methods -----
    def role(self):
        cash_info = self.holdings["cash"]
        units_info = self.holdings["markets"][self._market_id]
        self.inform(cash_info)
        self.inform(units_info)
        if cash_info["cash"] == 0:
            self.inform("Bot is a seller")
            return Role(1)
        else:
            self.inform("Bot is a buyer")
            return Role(0)

    def order_accepted(self, order):
        self.inform("Order with ref " + str(order.ref) + "was accepted in market " + str(self._market_id))
        MyOrder.status = OrderStatus["ACCEPTED"]
        pass

    def order_rejected(self, info, order):
        self.inform("Order with ref " + str(order.ref) + "was rejected in market " + str(self._market_id))
        MyOrder.status = OrderStatus["REJECTED"]
        pass

    def _print_trade_opportunity(self, other_order):
        """
        Depending on our role and our bottype, print trade opportunity accordingly
        :param other_order: trade opportunities seen
        :return: self.inform() - let user know there is a good trade opportunity
        """
        self.inform("My Role is " + str(self._role) +
                    ". Current best trade opportunity would be buying at $" + str(other_order / 100))
        if self._bot_status == BotStatus["UNABLE_UNITS_MAX"] or self._bot_status == BotStatus["UNABLE_CASH_ZERO"]:
            if self._role == Role["BUYER"]:
                if self._bot_status == BotStatus["UNABLE_UNITS_MAX"]:
                    self.inform("Buyer has already bought 5 units.")
                elif self._bot_status == BotStatus["UNABLE_CASH_ZERO"]:
                    self.inform("Buyer has no more available cash left.")
            elif self._role == Role["SELLER"]:
                self.inform("Seller has no more available units left.")

    # ------ End of Helper and trivial methods -----


class MyOrder:
    """
    This class should be implemented to have a better storage of current and past orders. And packing and sending
    orders will also be better implemented in this class, also interact with MyMarkets class
    """

    def __init__(self, price, units, order_type, order_side, market_id):
        now = time.strftime(TIME_FORMATTER, time.localtime())  # year-month-day-hour-minute-second
        ref = ORDER_TYPE_TO_CHAR[order_type] + SEPARATION + ORDER_SIDE_TO_CHAR[order_side] + SEPARATION + str(now)
        self.price = price
        self.units = units
        self.order_type = order_type
        self.order_side = order_side
        self.market_id = market_id
        self.ref = ref
        self.order_status = OrderStatus["MAKING"]
        self.sent_order = None
        # self.cancel_order = None

    def make_order(self, agent=None):
        if agent:
            agent.inform("Making Order with ref" + self.ref)
        return Order(self.price, self.units, self.order_type, self.order_side, self.market_id, ref=self.ref)

    def send_order(self, agent):
        if agent.status == OrderStatus["MAKING"]:
            self.to_be_sent_order = self.make_order()
            agent.inform("Sending Order with ref" + self.ref)
            agent.status = OrderStatus["PENDING"]
            agent.send_order(self.to_be_sent_order)
            agent.inform('Sent Order with ref' + self.ref)
        # found a more profitable trade, cancel previous to make new
        elif agent.status == OrderStatus["ACCEPTED"]:
            pass

    def cancel_sent_order(self):
        # if self.status in []
        pass

    def compare_order(self, other_order):
        if self.ref == other_order.ref:
            return True
        pass


class MyMarkets:
    """
    Market class that can parse market from dictionary form to a class form and provide extra-functionality to support
    putting orders into market.
    """

    def __init__(self, market_dict, logger_agent=None):
        if logger_agent:
            logger_agent.inform("Start converting market")
            logger_agent.inform("Currently logging market: " + repr(list(market_dict.items())))
        # These are only given property getter no other handles, for they are not supposed to be changed
        self.dict = market_dict
        self._time = time.time()
        self._id = market_dict["id"]
        self._min = market_dict["minimum"]
        self._max = market_dict["maximum"]
        self._tick = market_dict["tick"]
        self._name = market_dict["name"]
        self._item = market_dict["item"]
        self._description = market_dict["description"]

    @property
    def time(self):
        return self._time

    @property
    def id(self):
        return self._id

    @property
    def min(self):
        return self._min

    @property
    def max(self):
        return self._max

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

    def verify_order(self, order):
        pass


if __name__ == "__main__":
    FM_ACCOUNT = "bullish-delight"
    FM_EMAIL = "z.huang51@student.unimelb.edu.au"
    junda = "j.lee161@student.unimelb.edu.au"
    FM_PASSWORD = "908525"
    junda_pass = "888086"
    MARKETPLACE_ID = 260  # replace this with the marketplace id

    ds_bot = DSBot(FM_ACCOUNT, junda, junda_pass, MARKETPLACE_ID)
    ds_bot.run()
