from decimal import Decimal


def toNearest(num, tickSize):
    """Given a number, round it to the nearest tick. Very useful for sussing float error
       out of numbers: e.g. toNearest(401.46, 0.01) -> 401.46, whereas processing is
       normally with floats would give you 401.46000000000004.
       Use this after adding/subtracting/multiplying numbers."""
    if tickSize > 0:
        tickDec = Decimal(str(tickSize))
        return float((Decimal(round(num / tickSize, 0)) * tickDec))
    else:
        return int(num)


def roundQuantity(qty, minOrderLog = None):
    if minOrderLog is not None:
        return round(qty, minOrderLog)
    else:
        if abs(qty) < 1:
            return round(qty, 5)
        else:
            return int(qty)


def get_decimal_digits_number(decimal_val):
    return Decimal(str(decimal_val)).as_tuple().exponent * -1


def get_round_value(value, tick_log):
    if abs(value) < 1 and tick_log == 0:
        return round(value, 8)
    elif abs(value) < 1 and tick_log > 0:
        return round(value, 8)
    elif abs(value) >= 1:
        return round(value, tick_log)