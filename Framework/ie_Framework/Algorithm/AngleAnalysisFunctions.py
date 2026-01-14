"""Piezo angle analysis """

import cmath
import math

import numpy as np
from scipy.optimize import curve_fit
from scipy.special import erf

# General input parameters
PITCH = 33  # Pitch in um in the grating plane
PIXEL_SIZE = 3.33  # Pixel size in um
THEORIE_WINKEL_DEG = 8.95  # Expected angle of the first order to the x-axis in deg

# Set0Start = 1999  # Start rows at 100 if only image stripes should be measured.
# Set0End = 1999  # End rows at 100


def crop_center(img, cropx, cropy, shiftDy=0, shiftDx=0):
    """Return a centered crop of `img` with optional pixel shifts."""
    y, x = img.shape
    startx = x // 2 - (cropx // 2)
    starty = y // 2 - (cropy // 2)
    return img[
        starty - shiftDy : starty - shiftDy + cropy,
        startx - shiftDx : startx - shiftDx + cropx,
    ]


def remove_phase_jumps(phase_data):
    """Remove +/- 2*pi phase jumps from a 1D phase sequence."""
    diff = np.diff(phase_data)

    # Identify large jumps (greater than pi or less than -pi)
    jumps = np.abs(diff) > np.pi
    _ = jumps  # keep variable for potential debugging

    # Correct the phase values
    for i in range(0, len(diff)):
        if np.abs(diff[i]) > np.pi / 1:
            diff[i] = (
                phase_data[i]
                - phase_data[i + 1]
                - np.sign(phase_data[i] - phase_data[i + 1]) * 2 * np.pi
            )

    return diff


def erf_model(x, a, b, c, d):
    """Error function model used for edge fitting."""
    return a * erf(b * x + c) + d


def calc_grating_fft_phases_frequencies(frame, shiftDy=0, shiftDx=0):
    """Calculate FFT phases and peak indices for the three main grating orders."""
    large_array = np.zeros((6000, 6000))
    small_array = crop_center(frame, 1000, 1000, shiftDy, shiftDx)

    start_x = (large_array.shape[0] - small_array.shape[0]) // 2
    start_y = (large_array.shape[1] - small_array.shape[1]) // 2

    # Place the cropped array into the large array
    large_array[
        start_x : start_x + small_array.shape[0],
        start_y : start_y + small_array.shape[1],
    ] = small_array

    img_arr = large_array
    # img_arr[Set0Start:Set0End] = 100  # Option: zero-out rows for partial image analysis

    # Calculate Fourier transform of the padded image
    ft = np.fft.ifftshift(img_arr)
    ft = np.fft.fft2(ft)
    ft = np.fft.fftshift(ft)

    Absolut = abs(ft)

    # Find maxima of the orders within the defined FFT regions
    Ord_1 = Absolut[3000:6000, 3500:6000]  # indices [row, column]
    Ord_3 = Absolut[0:2500, 0:3000]
    Ord_2 = Absolut[0:2700, 3000:6000]
    i, k = np.unravel_index(Ord_1.argmax(), Ord_1.shape)
    l, m = np.unravel_index(Ord_3.argmax(), Ord_3.shape)
    n, o = np.unravel_index(Ord_2.argmax(), Ord_2.shape)

    print("n and o")
    print((3000 - n), o)

    Phase_1 = cmath.phase(ft[i + 3000, k + 3500])
    Phase_2 = cmath.phase(ft[n, o + 3000])
    Phase_3 = cmath.phase(ft[l, m])

    return Phase_1, Phase_2, Phase_3, i, k, l, m, n, o


def SingleImageGratingAngle(frame, shiftDy=0, shiftDx=0):
    """Calculate grating angle from FFT of a single frame."""
    frame_rot90CCW = np.rot90(frame, k=1, axes=(0, 1))
    print("Shape of rotated stack is :,", frame_rot90CCW.shape)

    # Run FFT frequency/phase analysis on the frame
    (
        Phasenliste_1,
        Phasenliste_2,
        Phasenliste_3,
        index_i,
        index_k,
        index_l,
        index_m,
        index_n,
        index_o,
    ) = calc_grating_fft_phases_frequencies(frame_rot90CCW)

    # Compute grating angles from the three orders
    (
        AVGWinkel,
        Winkel_1,
        Winkel_2,
        Winkel_3,
        Winkel_1_Nr2,
        Winkel_1Mess,
        WinkelCam_Fehler,
    ) = calc_grating_angle_from_fft_freq(index_i, index_k, index_l, index_m, index_n, index_o)

    return AVGWinkel


def calc_grating_angle_from_fft_freq(index_i, index_k, index_l, index_m, index_n, index_o):
    """Calculate grating angle relative to the camera from FFT order indices."""
    Winkel_3 = math.degrees(math.atan((3000 - index_m) / (3000 - index_l)))
    Winkel_1 = 120 - Winkel_3 - 90
    Winkel_1Mess = math.degrees(math.atan((index_i / (index_k + 500))))
    Winkel_1_Nr2 = 60 + math.degrees(math.atan((index_o / (3000 - index_n)))) - 90
    Winkel_2 = math.degrees(math.atan((index_o / (3000 - index_n))))

    AVGWinkel = (Winkel_1_Nr2 + Winkel_1 + Winkel_1Mess) / 3
    WinkelCam_Fehler = AVGWinkel - THEORIE_WINKEL_DEG

    print("Grating angle to camera in deg from order 1-3:")
    print(Winkel_1)
    print("Grating angle to camera in deg from order 1-1:")
    print(Winkel_1Mess)
    print("Grating angle to camera in deg from order 1-2:")
    print(Winkel_1_Nr2)
    print("Average grating angle to camera in deg:")
    print(AVGWinkel)

    print("Grating angle error to camera in deg (clockwise positive):")
    print(WinkelCam_Fehler)

    return AVGWinkel, Winkel_1, Winkel_2, Winkel_3, Winkel_1_Nr2, Winkel_1Mess, WinkelCam_Fehler


def AnalysePiezoAngleGratingEdge(shiftstack, shiftDy=0, shiftDx=0):
    """Compute piezo angle using edge-based ERF fits and FFT-derived grating angles."""
    piezo_angle = 0
    num_frames = shiftstack.shape[0]

    shiftstack_rot90CCW = np.rot90(shiftstack, k=1, axes=(1, 2))
    print("Shape of rotated stack is :,", shiftstack_rot90CCW.shape)

    # Crop the center of each frame
    center_array = []
    for i in range(num_frames):
        center_array.append(crop_center(shiftstack_rot90CCW[i], 400, 400, shiftDy, shiftDx))

    # Run FFT frequency/phase analysis on the first frame
    (
        Phasenliste_1,
        Phasenliste_2,
        Phasenliste_3,
        index_i,
        index_k,
        index_l,
        index_m,
        index_n,
        index_o,
    ) = calc_grating_fft_phases_frequencies(shiftstack_rot90CCW[0])

    # Compute grating angles from the three orders
    (
        AVGWinkel,
        Winkel_1,
        Winkel_2,
        Winkel_3,
        Winkel_1_Nr2,
        Winkel_1Mess,
        WinkelCam_Fehler,
    ) = calc_grating_angle_from_fft_freq(index_i, index_k, index_l, index_m, index_n, index_o)

    # Determine shift length in the vertical direction
    mittelwerte_zeilen0 = np.mean(center_array[0], axis=1)
    mittelwerte_zeilenEnd = np.mean(center_array[num_frames - 1], axis=1)

    # ERF fit for the first frame vertical edge
    x_data = np.linspace(0, 400, 400)
    y_data = mittelwerte_zeilen0
    params, covariance = curve_fit(erf_model, x_data, y_data, p0=[1, 0.5, 0.5, 0.5])
    a_fit, b_fit, c_fit, d_fit = params
    _ = (a_fit, d_fit)  # keep for potential debugging
    PosV0 = c_fit / b_fit

    # ERF fit for the last frame vertical edge
    x_data = np.linspace(0, 400, 400)
    y_data = mittelwerte_zeilenEnd
    params, covariance = curve_fit(erf_model, x_data, y_data, p0=[1, 0.5, 0.5, 0.5])
    a_fit, b_fit, c_fit, d_fit = params
    _ = (a_fit, d_fit)
    PosVEnd = c_fit / b_fit

    # Vertical shift output
    SchubV = PosVEnd - PosV0
    print("Vertical shift")
    print(SchubV)

    # Determine shift length in the horizontal direction
    center_array_0shift = center_array[0]
    center_array_Endshift = center_array[num_frames - 1]

    center_array_0shift = center_array_0shift[(int(SchubV)) :]
    center_array_Endshift = center_array_Endshift[: -(int(SchubV))]

    mittelwerte_spalten0 = np.mean(center_array_0shift, axis=0)
    mittelwerte_spaltenEnd = np.mean(center_array_Endshift, axis=0)

    # ERF fit for the bottom edge
    x_data = np.linspace(400, 0, 400)
    y_data = mittelwerte_spalten0
    params, covariance = curve_fit(erf_model, x_data, y_data, p0=[1, 0.5, 0.5, 0.5])
    a_fit, b_fit, c_fit, d_fit = params
    _ = (a_fit, d_fit)
    PosH0 = c_fit / b_fit

    # ERF fit for the top edge
    x_data = np.linspace(400, 0, 400)
    y_data = mittelwerte_spaltenEnd
    params, covariance = curve_fit(erf_model, x_data, y_data, p0=[1, 0.5, 0.5, 0.5])
    a_fit, b_fit, c_fit, d_fit = params
    _ = (a_fit, d_fit)
    PosHEnd = c_fit / b_fit

    # Horizontal shift
    SchubH = PosHEnd - PosH0
    print("Horizontal shift")
    print(SchubH)

    # Angle derived from horizontal and vertical shifts
    VWinkel = math.degrees(math.atan(SchubH / SchubV))
    VWinkelFehler = -1 * (((Winkel_1 + Winkel_1Mess + Winkel_1_Nr2) / 3) - THEORIE_WINKEL_DEG - VWinkel)

    print("Shift vector to camera in deg (clockwise positive)")
    print(VWinkel)
    print("Shift vector relative to grating in deg (clockwise positive)")
    print(VWinkelFehler)

    piezo_angle = VWinkel
    return piezo_angle, AVGWinkel


# PIEZO ANGLE FUNCTION BY FFT PHASE SHIFT ANALYSIS
# ------------------------------------------------


def AnalysePiezoAngleFFT(shiftstack):
    """Compute piezo angle using phase shifts of FFT orders across frame stack."""
    shiftD = 5  # Offset for evaluated portion of the image (unused currently)
    _ = shiftD
    piezo_angle = 0
    num_frames = shiftstack.shape[0]

    # Rotate stack 90 degrees CCW
    shiftstack_rot90CCW = np.rot90(shiftstack, k=1, axes=(1, 2))
    print("Shape of rotated stack is :,", shiftstack_rot90CCW.shape)

    Phasenliste_1 = np.zeros(num_frames)
    Phasenliste_2 = np.zeros(num_frames)
    Phasenliste_3 = np.zeros(num_frames)

    for z in range(num_frames):
        (
            Phasenliste_1[z],
            Phasenliste_2[z],
            Phasenliste_3[z],
            index_i,
            index_k,
            index_l,
            index_m,
            index_n,
            index_o,
        ) = calc_grating_fft_phases_frequencies(shiftstack_rot90CCW[z])

    # Debugging tools
    print("Phasenliste_2")
    print(Phasenliste_2)

    # Generate phase differences in um at the grating and remove 2*pi phase jumps
    Phasensteps_1c = np.abs(remove_phase_jumps(Phasenliste_1)) * PITCH / (2 * np.pi)
    Phasensteps_3c = np.abs(remove_phase_jumps(Phasenliste_3)) * PITCH / (2 * np.pi)
    Phasensteps_2c = np.abs(remove_phase_jumps(Phasenliste_2)) * PITCH / (2 * np.pi)

    Phasensteps_1c_tot = np.sum(Phasensteps_1c)
    Phasensteps_3c_tot = np.sum(Phasensteps_3c)
    Phasensteps_2c_tot = np.sum(Phasensteps_2c)

    print("Phase steps 2")
    print(Phasensteps_2c)

    # Determine grating angles and displacement vector
    (
        AVGWinkel,
        Winkel_1,
        Winkel_2,
        Winkel_3,
        Winkel_1_Nr2,
        Winkel_1Mess,
        WinkelCam_Fehler,
    ) = calc_grating_angle_from_fft_freq(index_i, index_k, index_l, index_m, index_n, index_o)

    Vektor1 = np.array([math.cos(math.radians(Winkel_1)), (-1) * math.sin(math.radians(Winkel_1))])
    Vektor3 = np.array([(-1) * math.sin(math.radians(Winkel_3)), (1) * math.cos(math.radians(Winkel_3))])
    Vektor2 = np.array([(-1) * math.sin(math.radians(Winkel_2)), (1) * math.cos(math.radians(Winkel_2))])

    # Displacement vector from orders 1-3 and 1-1
    A = np.array([Vektor1, Vektor3])
    B = np.array([(-1) * Phasensteps_1c_tot, Phasensteps_3c_tot])
    X = np.linalg.solve(A, B)

    V_Winkel = math.degrees(math.atan(X[0] / X[1]))
    V_Winkel_Fehler = -1 * (WinkelCam_Fehler - V_Winkel)

    # Displacement vector from orders 1-3 and 1-2 (still to be refined)
    A2 = np.array([Vektor2, Vektor3])
    B2 = np.array([(-1) * Phasensteps_2c_tot, Phasensteps_3c_tot])
    X2 = np.linalg.solve(A2, B2)

    V_Winkel2 = math.degrees(math.atan(X2[0] / X2[1]))
    V_Winkel_Fehler2 = -1 * (WinkelCam_Fehler - V_Winkel2)
    _ = V_Winkel_Fehler2

    print("V-Winkel2")
    print(V_Winkel2)

    print("Grating angle to camera in deg from order 1-3:")
    print(Winkel_1)
    print("Grating angle to camera in deg from order 1-1:")
    print(Winkel_1Mess)
    print("Grating angle error to camera in deg (clockwise positive):")
    print(WinkelCam_Fehler)
    print("Displacement vector:")
    print(X)
    print("Displacement angle in deg:")
    print(V_Winkel)
    print("Displacement angle error to grating in deg (clockwise positive):")
    print(V_Winkel_Fehler)

    piezo_angle = V_Winkel
    return piezo_angle, AVGWinkel
