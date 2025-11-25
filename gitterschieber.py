import random
import cv2
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import serial
import threading
import time
from PIL import Image, ImageEnhance, ImageFilter
import pandas as pd
import seaborn as sns
import os
import sys
import pathlib
BASE_DIR = pathlib.Path(__file__).resolve().parent
for _sub in ("Hardware", "Algorithmen"):
    _cand = BASE_DIR / _sub
    if _cand.exists():
        sys.path.insert(0, str(_cand))


from ie_Framework.Hardware.Camera.DinoLiteController import DinoLiteController, DummyDinoLite
from ie_Framework.Hardware.Motor.EightMotorcontroller import MotorController
from ie_Framework.Algorithm.AngleAnalysisFunctions import (AnalysePiezoAngleFFT,
    AnalysePiezoAngleGratingEdge,
    SingleImageGratingAngle)

import tifffile as tif


#unterdrücke Plots
matplotlib.use('Agg')

####    CONSTANTS       ####    
STAGE_WAIT_TIME = 1e-4      # wait time of 1 s per 10000 stage steps, in order to assure that we have arrived at position
volt_addr = 0xD0 # address for voltage channel on DAC
volt_pltf = 0xC0 # platform for voltage channel on DAC

startpos_x = 2000
startpos_y = 0
startpos_z = 3000 
working_distance_dif = 32000
particle_count=0
particle_diameters = []

THEORIE_WINKEL_DEG=8.95 #Winkel der ersten Ordnung Nr. 1 zur x Achse in° (SIM 31 grating)
GRATING_ERROR_TOLERANCE_MRAD = 2 # tolerance of 2 mrad for grating to piezo shift angle

SIM7_centerpos = [118395, 154950] # x,y
#SIM31_centerpos = [118395, 263010] # x,y
SIM31_centerpos = [81525, 151110] # x,y
SIM31_SEcornerpos = [85620, 298950]
currentPosXYZ = [0, 0, 0]   # to check what current positions are

maxVolt = 4.022 * 1e3 # maximum voltage output * 1000
x_addr = 18
y_addr = 19
z_addr = 20
voltage_step = 0.166 * 1e3  # 0.333 V ca. 10 um stepsize
OP_amp_factor = 2       # amplification of voltage

piezoshift_angle_to_cam = 100 # piezo angle to camera in degrees
grating_angle_to_cam = 100 # angle of sim 31 grating to camera
grating_angle_error = 100 # error of sim grating angle to piezo shift angle in degrees
angle_processing_active = False
gui = None

# EightMotorcontroller normal importieren (Wrapper-Modul EightMotorcontroller)
class _NullStage:
    """Fallback-Stage, damit GUI auch ohne Hardware startet."""
    def __init__(self): self.connected = False
    def move_to_pos(self, *a, **k): print("[WARN] Stage nicht verbunden, move_to_pos ignoriert.")
    def set_analog_output(self, *a, **k): print("[WARN] Stage nicht verbunden, set_analog_output ignoriert.")
    def current_pos(self, *a, **k): print("[WARN] Stage nicht verbunden, current_pos=0."); return 0

try:
    stage = MotorController(port_number=5, baud_rate=9600, verbose=True)
    stage.connected = True
    stage_status_text = "Stage verbunden (COM5)"
except Exception as exc:
    stage = _NullStage()
    stage_status_text = f"Stage nicht verbunden: {exc}"



####    FUNCTIONS   ####

def SelectImgDir():
    global filedir
    if 'filedir' not in globals():
        return
    if 'QFileDialog' in globals() and QFileDialog is not None:
        pathselect = QFileDialog.getExistingDirectory(None, "Select image directory", str(filedir))
        if pathselect:
            print(pathselect)
            filedir = pathselect
    else:
        print("QFileDialog nicht verfuegbar; nutze aktuelles Verzeichnis.")

def wait_time(oldPos, newPos):
    """Return waittime based on how far stage has to move"""
    stageDisplacement = abs(newPos - oldPos)
    sleepTime = stageDisplacement*STAGE_WAIT_TIME
    print("Will sleep for {} seconds.".format(round(sleepTime, 3)))
    return sleepTime




def update_position(val):
    global pos
    pos = int(val)
    stage.move_to_pos(y_addr, pos, True)
    position_label.config(text=f"Position: {pos}")

def start_live_feed():
    live_feed_thread = threading.Thread(target=dino_lite.show_live_feed)
    live_feed_thread.start()

def update_frame():
    # Placeholder: UI-Anzeige wird in stagetest.py gehandhabt
    return

def process_image(stitched_image):
    focus_frame = stitched_image
    #focus_frame = autofocus()
    cv2.imwrite('focused_image.tif', focus_frame)
    fourier_img = fourier(focus_frame)
    Image.fromarray(fourier_img).save('fourier_square.tif')
    thresh_image, detected_particles = count_particles(fourier_img,focus_frame)
    cv2.imwrite('tresh.tif', thresh_image)
    Image.fromarray(cv2.cvtColor(detected_particles, cv2.COLOR_BGR2RGB)).save('detected.tif')
    
    return focus_frame  

def count_particles(fourier_frame, focus_frame):
    global particle_count, particle_diameters

    thresh_image = (fourier_frame > 90).astype(np.uint8) * 255
    contours, _ = cv2.findContours(thresh_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    particle_count = 0
  
    for contour in contours:
        particle_count += 1
        (x, y), radius = cv2.minEnclosingCircle(contour)
        diameter_um = 2 * radius * 2.6
        particle_diameters.append(diameter_um)
        cv2.circle(focus_frame, (int(x), int(y)), int(radius), (0, 255, 0), 2)
        if diameter_um >= 5:
            cv2.circle(focus_frame, (int(x), int(y)), int(radius), (0, 0, 255), 2)
  
    #print(particle_diameters)
    
    create_particle_plot(particle_diameters,focus_frame)
    
    return thresh_image, focus_frame

fig = None
axes = None

def init_plot():
    global fig, axes
    plt.ion()
    fig, axes = plt.subplots(1, 3, figsize=(14, 7))
    #plt.show()

def create_particle_plot(particle_diameters, focus_frame):
    global fig, axes

    threshold_um = 5
    small_particles = [d for d in particle_diameters if d < threshold_um]
    large_particles = [d for d in particle_diameters if d >= threshold_um]
    total_particles = len(particle_diameters)
    small_count = len(small_particles)
    large_count = len(large_particles)

    axes[0].clear()
    sns.histplot(particle_diameters, bins=20, kde=False, color="blue", ax=axes[0])
    axes[0].axvline(x=threshold_um, color='red', linestyle='--', label=f'Grenzwert: {threshold_um} µm')
    axes[0].set_title(f'Histogramm der Partikeldurchmesser\n'
                      f'Insgesamt: {total_particles}, < {threshold_um} µm: {small_count}, ≥ {threshold_um} µm: {large_count}')
    axes[0].set_xlabel('Durchmesser (µm)')
    axes[0].set_ylabel('Anzahl der Partikel')
    axes[0].legend()
    axes[1].clear()
    sns.boxplot(particle_diameters, color="lightblue", ax=axes[1])
    axes[1].axvline(x=threshold_um, color='red', linestyle='--', label=f'Grenzwert: {threshold_um} µm')
    axes[1].set_title(f'Boxplot der Partikeldurchmesser\n'
                      f'Insgesamt: {total_particles}, < {threshold_um} µm: {small_count}, ≥ {threshold_um} µm: {large_count}')
    axes[1].set_xlabel('Durchmesser (µm)')
    
    axes[2].clear()
    axes[2].imshow(cv2.cvtColor(focus_frame, cv2.COLOR_BGR2RGB))
    axes[2].axis('off')
    axes[2].set_title('Detected Particles')

   
    plt.tight_layout()

    ts = time.strftime("%Y%m%d_%H%M%S")
    outfile = os.path.join(filedir, f"particle_plot_{ts}.png")
    fig.savefig(outfile, dpi=300)

    #plt.draw()
    #plt.pause(0.1)


    

def fourier(frame):
    frame_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    f = np.fft.fft2(frame_gray)
    fshift = np.fft.fftshift(f)
    rows, cols = frame_gray.shape
    crow, ccol = rows // 2, cols // 2
    mask_size = 10
    mask = np.ones((rows, cols), np.uint8)
    mask[crow-mask_size:crow+mask_size, ccol-mask_size:ccol+mask_size] = 0
    fshift_filtered = fshift * mask
    f_ishift = np.fft.ifftshift(fshift_filtered)
    img_back = np.fft.ifft2(f_ishift)
    img_back = np.abs(img_back)
    img_back_normalized = np.uint8(255 * img_back / np.max(img_back))

    return img_back_normalized

def autofocus():
    global z_slices 
    z_slices = []
    
    beste_fokus_bewertung = -1
    
    focus_range = 2000
    focus_position = stage.current_pos(20)
    startpos = int(stage.current_pos(20) - focus_range / 2)
    endpos = int(stage.current_pos(20) + focus_range / 2)
    focus_frame = None

    stage.move_to_pos(z_addr, int(startpos), False)
    time.sleep(0.5)

    for pos in range(startpos, endpos+40, 100):
        
        frame = dino_lite.capture_image()
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        z_slices.append(frame)

        fokus_bewertung = cv2.Laplacian(frame, cv2.CV_64F).var()
        
        if fokus_bewertung > beste_fokus_bewertung:
            beste_fokus_bewertung = fokus_bewertung
            focus_position = stage.current_pos(20)
            focus_frame = frame
            #print("Found new focus at {} steps.".format(focus_position))

        stage.move_to_pos(z_addr, pos, False)
    

    
    stage.move_to_pos(z_addr, focus_position, False)
    time.sleep(1)
    focus_frame = dino_lite.capture_image()
    

    return focus_frame

def autofocusblocked():
    print("Autofocus is currently blocked. Z motor cannot be used!")



points = []

def click_event(event, x, y, flags, params):
    if event == cv2.EVENT_LBUTTONDOWN:
        points.append((x, y))
        cv2.circle(img, (x, y), 5, (0, 255, 0), -1)
        cv2.imshow("Bild", img)
        
        if len(points) == 2:
            dist = ((points[0][0] - points[1][0]) ** 2 + (points[0][1] - points[1][1]) ** 2) ** 0.5
            print(f"Abstand zwischen den Punkten in Pixeln: {dist} Pixel")

def measure_distance():
    global img
    img = cv2.imread("detected.tif")
    cv2.imshow("Bild", img) 
    cv2.setMouseCallback("Bild", click_event)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    

def stitch():
    imagesy = []
    imagesx = []

    # start_y, end_y = 215000, 110000
    start_y, end_y = startpos_y, 224620

    # for posx in range(0, 120000, 13500):
    for posx in range(startpos_x, startpos_x + 120000, 13500):
        stage.move_to_pos(x_addr, posx, False)
        time.sleep(1)

        for posy in range(start_y, end_y, -9000 if start_y > end_y else 9000):
            stage.move_to_pos(y_addr, posy, True)
            frame = autofocus()
            # frame = dino_lite.capture_image()
            time.sleep(1)

            if start_y < end_y:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            imagesy.append(frame)
            process_image(frame)

        y_image = cv2.vconcat(imagesy)
        y_image = cv2.rotate(y_image, cv2.ROTATE_180)
        imagesx.append(y_image)
        imagesy.clear()
        start_y, end_y = end_y, start_y

    stitched_image = cv2.hconcat(imagesx)
    process_image(stitched_image)
    combined_image_rgb = cv2.cvtColor(stitched_image, cv2.COLOR_BGR2RGB)
    Image.fromarray(combined_image_rgb).save("Combined_Image.tif", format='TIFF', compression='none')
    create_particle_plot(particle_diameters, stitched_image)
        

def startpos():
    stage.move_to_pos(x_addr, startpos_x, False) # x
    time.sleep(2)
    stage.move_to_pos(y_addr, startpos_y, True) # y
    time.sleep(2)
    stage.move_to_pos(z_addr, startpos_z, False) # z DONT USE!
    #time.sleep(5)
    #move_to_pos(18, 40000)
    #move_to_pos(19, 175000)
    #move_to_pos(20, 60000) 
    
def angle():
    # currently unused
    stage.move_to_pos(19, 210000, True)
    time.sleep(6)
    image_start = dino_lite.capture_image()
    least_squares_sums = []
    positions = list(range(0, 5000, 100))
    for posy in positions:
        image_pos = dino_lite.capture_image()
        cv2.imwrite(f'C:/Users/Lvbttest/Desktop/Angle/angle_{posy}.tif', image_pos)
        
        difference = image_start.astype(np.float32) - image_pos.astype(np.float32)
        squared_difference = np.square(difference)
        least_squares_sum = np.sum(squared_difference)
        print(f"Position {posy}, Least Squares Sum: {least_squares_sum}")
        least_squares_sums.append(least_squares_sum)
        stage.set_analog_output(0xD7,0xC0, posy)
        time.sleep(1)
    
    min_index = np.argmin(least_squares_sums)
    min_value = least_squares_sums[min_index]

    df = pd.DataFrame({'Position': positions,'LeastSqaure': least_squares_sums})
    df.to_excel('Rohdaten.xlsx')
    plt.plot(positions, least_squares_sums, marker='o', linewidth=0.1)
    
    plt.title('Least Squares Summen für verschiedene Positionen')
    plt.xlabel('Position')
    plt.ylabel('Least Squares Summe')
    plt.grid(True)
    plt.scatter(positions[min_index], min_value, color='red', zorder=5)
    plt.annotate(f'Min: {min_value:.2f}', (positions[min_index], min_value), textcoords="offset points", xytext=(0,10), ha='center', color='red')
    plt.show()

def GratingShiftwSave():
    #testing function
    print("Starting an angle measurement!")
    dirname = "/piezo_angle_meas"
    # move to center of SIM31 grating
    stage.move_to_pos(x_addr, SIM31_SEcornerpos[0], False)
    stage.move_to_pos(y_addr, SIM31_SEcornerpos[1], True)    
    time.sleep(3)
    voltage_range = np.arange(0, maxVolt*OP_amp_factor, step=voltage_step*OP_amp_factor, dtype=int)
    os.mkdir(filedir + dirname)
    for i in range(len(voltage_range)):
        print("New voltage: {} V".format(voltage_range[i]))
        stage.set_analog_output(volt_addr, volt_pltf, int(voltage_range[i]/OP_amp_factor))
        time.sleep(0.5)
        new_image = dino_lite.capture_image()
        cv2.imwrite(filedir + dirname + f"\\Voltage_{voltage_range[i]}mV.tif", new_image)

    print("Finished acquiring voltage steps!")
    stage.set_analog_output(volt_addr, volt_pltf, 0)


def acquire_single_frame():
    """Acquire single frame for grating angle measurement"""
    new_image = dino_lite.capture_image()
    res_frame = np.zeros((new_image.shape[0], new_image.shape[1]), dtype="uint16")
    for z in range(3):
            res_frame += new_image[:,:,z]
    return res_frame
    
def acquire_shiftstack(voltage_range):
    """ Acquisition function for piezo grating shift angle measurements"""
    new_image = dino_lite.capture_image()    
    print("Captured test image, shape is:", new_image.shape)
    pxly, pxlx, pxlz  = new_image.shape
    shiftstack = np.zeros((len(voltage_range), pxly, pxlx), dtype="uint16")
    print("Shape of stack is:", shiftstack.shape)
    
    for i in range(len(voltage_range)):
        print("New voltage: {} V".format(voltage_range[i]))
        stage.set_analog_output(volt_addr, volt_pltf, int(voltage_range[i]/OP_amp_factor))
        time.sleep(0.5)
        new_image = dino_lite.capture_image()
        # sum each channel to crate BW image.
        for z in range(3):
            shiftstack[i] += new_image[:,:,z]
        
    print("Finished acquiring voltage steps!")
    stage.set_analog_output(volt_addr, volt_pltf, 0)
    return shiftstack



def MeasureShiftFFT():
    print("Starting piezo angle measurement based on grating displacement and FFT")
    print("Starting an angle measurement!")
    # move to center of SIM31 grating
    stage.move_to_pos(z_addr, startpos_z + working_distance_dif, False) 
    
    stage.move_to_pos(x_addr, SIM31_centerpos[0], False)
    
    stage.move_to_pos(y_addr, SIM31_centerpos[1], True)
        
    autofocus() 
    time.sleep(2.5) # so everything can settle
    
    voltage_range = np.arange(0, maxVolt*OP_amp_factor, step=voltage_step*OP_amp_factor, dtype=int)
    # acquire test frame for shape
    shiftstack = acquire_shiftstack(voltage_range)
    
    # debugging tool
    #save_shiftstack(shiftstack, dirname="/FFTshift")

    print("Got stack! Proceeding with analysis. Using FFT")
    shiftstack = np.fliplr(shiftstack)
    #save_shiftstack(shiftstack, dirname="/finalY8-20250225")
    piezo_angle, grating_angle = AnalysePiezoAngleFFT(shiftstack)
    print("Piezo angle is: {} degrees.".format(piezo_angle))
    update_grating_angle_error(grating_angle, piezo_angle)
    print("Finished Shift measurement and analysis using FFT.")

    

def save_shiftstack(shiftstack, dirname="/Testmeasurement"):
    
    shiftstack_rot90CCW = np.rot90(shiftstack, k=1, axes=(1,2))
    os.mkdir(filedir+dirname)
    tif.imwrite(filedir+dirname+"/BWstack_rotated.tif", shiftstack_rot90CCW.astype('uint16'), photometric='minisblack')
    print("Saved stack!")
    print(filedir+dirname+"/BWstack_rotated.tif")


def MeasureShiftGratingEdge():
    print("Starting piezo angle measurement based on grating edge displacement")
    print("Starting an angle measurement!")
    # move to SE corner of SIM31 grating
    stage.move_to_pos(x_addr, SIM31_SEcornerpos[0], False)
    time.sleep(2.5)
    stage.move_to_pos(y_addr, SIM31_SEcornerpos[1], True)    
    time.sleep(5)

    voltage_range = np.arange(0, maxVolt*OP_amp_factor, step=voltage_step*OP_amp_factor*2, dtype=int)
    # acquire stack of shifted images
    shiftstack = acquire_shiftstack(voltage_range) 
    print("Got stack! Proceeding with analysis. Using error function of grating edge drop")

    shiftDy = -150     # parameter for shifting image ROI during analysis
    shiftDx = -200
    
    #for debugging
    #save_shiftstack(shiftstack, dirname="/GratingEdge")
    
    
    piezo_angle, grating_angle = AnalysePiezoAngleGratingEdge(shiftstack, shiftDy, shiftDx)
    
    print("Piezo angle is: {} degrees.".format(piezo_angle))
    update_grating_angle_error(grating_angle, piezo_angle)

def MeasureSingleImageGratingAngle():
    global piezoshift_angle_to_cam,grating_angle
    
    print("Measuring grating angle from single SIM 31 grating image.")
    # move to center of SIM31 grating
    stage.move_to_pos(y_addr, SIM31_centerpos[1], True)    
    time.sleep(1)
    stage.move_to_pos(x_addr, SIM31_centerpos[0], False)
    time.sleep(1)

    single_frame = acquire_single_frame()
    grating_angle  = SingleImageGratingAngle(single_frame)
    print("Grating angle is {} degrees.".format(grating_angle))
    update_grating_angle_error(grating_angle, piezoshift_angle_to_cam)


def liveAngle():
    global piezoshift_angle_to_cam,grating_angle

    single_frame = acquire_single_frame()
    single_frame = np.fliplr(single_frame)
    grating_angle  = SingleImageGratingAngle(single_frame)
    angle = update_grating_angle_error(grating_angle, piezoshift_angle_to_cam)
    if gui is not None:
        gui.label.setText(f"{angle}")
        gui.angle_dial.setValue(int(angle))
    return angle

def angle_processing():
    global angle_processing_active
    
    MeasureShiftFFT()
    if gui is not None:
        gui.pushButton.setStyleSheet("background-color: red; color: white;")
        gui.pushButton.setText("Winkel Justage stoppen")
    while angle_processing_active:
         liveAngle()
         fft_thread.msleep(100)

def start_angle_processing_thread():
    global fft_thread, angle_processing_active
    if gui is None or QThread is None:
        print("GUI/Qt nicht aktiv; angle_processing_thread wird nicht gestartet.")
        return
    fft_thread = QThread()
    fft_thread.run = angle_processing
    fft_thread.start()
    angle_processing_active = True
    gui.pushButton.setText("Kalibrierung läuft...")
    gui.pushButton.setStyleSheet("background-color: green; color: white;")

def stop_angle_processing_thread():
    global angle_processing_active
    if gui is None:
        angle_processing_active = False
        return
    angle_processing_active = False
    try:
        fft_thread.quit()
        fft_thread.wait()
    except Exception:
        pass
    gui.pushButton.setText("Winkel Justage starten")
    gui.pushButton.setStyleSheet("")

def toggle_angle_processing():
    if gui is None:
        print("GUI nicht aktiv; toggle_angle_processing ignoriert.")
        return
    if angle_processing_active:
        stop_angle_processing_thread()
    else:
        start_angle_processing_thread()

def update_grating_angle_error(grating_angle, shift_angle):
    """FUnction to update piezo and grating angles and their errors"""
    global grating_angle_error
    global piezoshift_angle_to_cam
    global grating_angle_to_cam

    piezoshift_angle_to_cam = shift_angle
    grating_angle_to_cam = grating_angle

    grating_angle_error = -1*(grating_angle_to_cam - THEORIE_WINKEL_DEG- piezoshift_angle_to_cam) # calculate error in degrees
    grating_angle_error_mrad = grating_angle_error *np.pi / 0.18
    print(f"New grating angle error is {round(grating_angle_error, 3)} degrees, or {round(grating_angle_error_mrad, 2)} mrad.")
    if (abs(grating_angle_error_mrad) < GRATING_ERROR_TOLERANCE_MRAD):
        print("Grating angle error is within tolerance of {} mrad!".format(GRATING_ERROR_TOLERANCE_MRAD))
    else:
        print("Tolerance not yet reached. Please adjust grating.")
        if grating_angle_error > 0:
            print("Adjust grating angle by turning grating in clockwise direction.")
        else:
            print("Adjust grating by turning grating in a counter-clockwise direction.")
    
    return grating_angle_error


init_plot()
#plt.show()
#measure_distance()

try:
    dino_lite = DinoLiteController()
except Exception as exc:
    print(f"[WARN] DinoLite-Kamera nicht verfuegbar: {exc}")
    dino_lite = DummyDinoLite(exc)

pos = 0
#startpos()
filedir = os.getcwd()

# PySide6 GUI für Livebild + Buttons
def capture_frame():
    """Return aktuelles Kamerabild (BGR) oder None bei Fehlern; für Embedding in stagetest."""
    try:
        return dino_lite.capture_image()
    except Exception as exc:
        print(f"[WARN] capture_frame fehlgeschlagen: {exc}")
        return None


def analyse_current_frame_particles():
    """Helper für stagetest: Partikelanalyse auf aktuellem Frame."""
    frame = capture_frame()
    if frame is None:
        return None
    process_image(frame.copy())
    return frame


def analyse_current_frame_angle():
    """Helper für stagetest: Winkelbestimmung auf aktuellem Frame (grau)."""
    frame = capture_frame()
    if frame is None:
        return None, None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    angle = SingleImageGratingAngle(gray)
    return frame, angle


# Keine GUI-Starts in diesem Modul; UI wird in stagetest.py eingebunden.


