"""
This file contains math functions for the mathematical process control. It can be replaced with an open source package
when one with support and documentation is available.
"""

import numpy as np


def calculateCpk(data: np.ndarray, lsl:float, usl:float):
    """
    Calculates the cpk-value for a given ndarray. This value indicates the capability of a process to manufacture within specific tolerance limits.

    cpk < 1.0 => Process not capable
    cpk > 1.0 => Process barely capable
    cpk > 1.33 => Minimum capability for many producers
    cpk > 1.67 => Good capability
    cpk > 2.0 => Excellent capability. Six Sigma level
    :param data: An ndarray containing a list of measurements
    :param lsl: Lower specific limit for measurement value. Anything lower is considered defective
    :param usl: Upper specific limit for measurement value. Anything above is considered defective
    :return: Returns an integer value which contains the calculated cpk-value
    """
    if data.size == 0: # No data given error
        return -10000 # Large negative values indicate an error.
    if usl < lsl: # limits not valid error
        return -20000
    x_mean = np.mean(data)
    if usl - x_mean > x_mean - lsl:
        cpk = (x_mean - lsl) / (3 * np.std(data))
    else:
        cpk = (usl - x_mean) / (3 * np.std(data))
    return cpk


def calculateCp(data: np.ndarray, lsl:float, usl:float):
    """
    The cp-value is a statistic value that shows the variation a process has in comparison to the allowed tolerance range.

    cp < 1.0 => Process not capable
    cp > 1.0 => Process barely capable
    cp > 1.33 => Minimum capability for many producers
    cp > 1.67 => Good capability
    cp > 2.0 => Excellent capability. Six Sigma level
    :param data: An ndarray containing a list of measurements
    :param lsl:Lower specific limit for measurement value. Anything lower is considered defective
    :param usl: Upper specific limit for measurement value. Anything above is considered defective
    :return: Returns an integer value which contains the calculated cp-value. Cp <= -10000 indicates an error
    """
    if data.size == 0: # No data given error
        return -10000 # Large negative values indicate an error.
    if usl < lsl: # limits not valid error
        return -20000
    cp = (usl - lsl) / (6 * np.std(data))
    return cp


