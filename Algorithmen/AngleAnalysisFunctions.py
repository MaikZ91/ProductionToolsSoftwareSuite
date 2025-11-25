""""Setting Andis piezo angle scripts into functions to be called by JustageGitterWinkel.py"""


import numpy as np
import PIL
from PIL import Image
import matplotlib.pyplot as plt
import pylab as plb
from scipy.optimize import curve_fit
import math
import cmath
from pathlib import Path
import matplotlib.pyplot as plt
from scipy.special import erf
import datetime
######Allgemeiner Input

PITCH=33 #Pitch in ym in der Gitterebene
PIXEL_SIZE=3.33 #Pixelgrösse in ym
THEORIE_WINKEL_DEG=8.95 #Winkel der ersten Ordnung Nr. 1 zur x Achse in°
  
#Set0Start=1999  #Beginn Bildzeilen auf 100 gestzt fals Bildausschnitte/Streifen vermessen werden sollen.
#Set0End=1999 #Ende Bildzeilen auf 100 gesetzt


####Funktion zentral Zuschneiden definieren 

def crop_center(img,cropx,cropy, shiftDy=0, shiftDx=0):
    y,x = img.shape
    startx = x//2-(cropx//2)
    starty = y//2-(cropy//2)    
    return img[starty-shiftDy:starty-shiftDy+cropy,startx-shiftDx:startx-shiftDx+cropx]

############# Function to Generate Phase Differences and remove 2pi phase jumps
def remove_phase_jumps(phase_data):
    # Compute the difference between consecutive points
    diff = np.diff(phase_data)
    
    #print('Differenzen')
    #print(diff)
    # Identify the large jumps (those greater than pi or less than -pi)
    jumps = np.abs(diff) > np.pi
    
    # Correct the phase values
    for i in range(0, len(diff)):
        if np.abs(diff[i]) > np.pi/1:
            diff[i] = phase_data[i] -phase_data[i+1]- np.sign(phase_data[i]-phase_data[i+1]) * 2 * np.pi
           
    
    return diff

# Define the error function model
def erf_model(x, a, b, c, d):
    return a * erf(b * x + c) + d

def calc_grating_fft_phases_frequencies(frame, shiftDy=0, shiftDx=0):
    """ part of the calculation for grating frequency"""
    large_array = np.zeros((6000, 6000))
    small_array=crop_center(frame,1000,1000, shiftDy, shiftDx)
    
    start_x = (large_array.shape[0] - small_array.shape[0]) // 2
    start_y = (large_array.shape[1] - small_array.shape[1]) // 2

    # Setze das kleine Array in das größere Array
    large_array[start_x:start_x + small_array.shape[0], start_y:start_y + small_array.shape[1]] = small_array    
    
    img_arr=large_array
        #img_arr[Set0Start:Set0End]=100  #Option: Hier können Zeilen 100 gesetzt werden zur analyse von Teilen des gesamt Bildes
   
    #### Calculate Fourier transform of 2000x2000px Bild
    #### Freuenzachse: Wellenlänge=Punktzahl/(Frequenzachse-Center)   

    ft = np.fft.ifftshift(img_arr)
    ft = np.fft.fft2(ft)
    ft = np.fft.fftshift(ft)

    Absolut=abs(ft)

        ####Maxima Finden Der Ordnungen im Bereich 1000-2000x1200-2000 (Ord Nr1) bzw 0-800x0-1000 (Ord Nr3) der FFT

    Ord_1=Absolut[3000:6000,3500:6000] #indizes [Zeile,Spalte] 
    Ord_3=Absolut[0:2500,0:3000] #indizes [Zeile,Spalte] 
    Ord_2=Absolut[0:2700,3000:6000] #indizes [Zeile,Spalte] 
    i,k = np.unravel_index(Ord_1.argmax(), Ord_1.shape)
    l,m = np.unravel_index(Ord_3.argmax(), Ord_3.shape)
    n,o = np.unravel_index(Ord_2.argmax(), Ord_2.shape)
    #print(Ord_3[i,k])
    #print(l,m) #indizes des Maximalen Frequenz Anteils
    print('n und o')
    print((3000-n),o)
   
    
        #print(cmath.phase(ft[i+1000,k+1200])) #Phasen der 1 Ordnung Nr1 (ganz rechts)
        #print(cmath.phase(ft[l,m])) #Phasen der 1 Ordnung Nr3 (ganz oben)
    
    Phase_1 = cmath.phase(ft[i+3000,k+3500])
    Phase_2 = cmath.phase(ft[n,o+3000])
    Phase_3 = cmath.phase(ft[l,m])

    return Phase_1, Phase_2, Phase_3, i,k, l,m, n,o

def SingleImageGratingAngle(frame, shiftDy=0, shiftDx=0):
    """ Calculate grating angle from fft of single frame"""
    frame_rot90CCW = np.rot90(frame, k=1, axes=(0, 1))
    print("Shape of rotated stack is :,", frame_rot90CCW.shape)

    # do all the exiting fft frequency and phase stuff for the first frame
    Phasenliste_1, Phasenliste_2, Phasenliste_3, index_i, index_k, index_l,index_m, index_n,index_o = calc_grating_fft_phases_frequencies(frame_rot90CCW)

    ##################Bestimmung der Winkel des Gitters aus den 3 Ordnungen##########################
    #################################################################################################  

    AVGWinkel, Winkel_1, Winkel_2, Winkel_3, Winkel_1_Nr2, Winkel_1Mess, WinkelCam_Fehler = calc_grating_angle_from_fft_freq(index_i,index_k, index_l,index_m, index_n,index_o)

    return AVGWinkel


def calc_grating_angle_from_fft_freq(index_i,index_k, index_l,index_m, index_n,index_o):
    """ calculates the grating angle relative to camera from the already found indices of the first frequency orders of the fft image"""
    Winkel_3=math.degrees(math.atan((3000-index_m)/(3000-index_l)))
    Winkel_1=120-Winkel_3-90
    Winkel_1Mess=math.degrees(math.atan((index_i/(index_k+500))))
    Winkel_1_Nr2=60+math.degrees(math.atan((index_o/(3000-index_n))))-90
    Winkel_2=math.degrees(math.atan((index_o/(3000-index_n))))      



    AVGWinkel=(Winkel_1_Nr2+Winkel_1+Winkel_1Mess)/3

    WinkelCam_Fehler=AVGWinkel-THEORIE_WINKEL_DEG

    ################################################

    print('Winkel des Gitters zur Kamera in ° aus Ord1-3:')
    print(Winkel_1)
    print('Winkel des Gitters zur Kamera in ° aus Ord1-1:')
    print(Winkel_1Mess)
    print('Winkel des Gitters zur Kamera in ° aus Ord1-2:')
    print(Winkel_1_Nr2)
    print('AVG Winkel des Gitters zur Kamera in °:')
    print(AVGWinkel)

    print('Winkel des Gitters zur Kamera Fehler in ° (Positiv im Urzeigersinn):')
    print(WinkelCam_Fehler)
    
    return AVGWinkel, Winkel_1, Winkel_2, Winkel_3, Winkel_1_Nr2, Winkel_1Mess, WinkelCam_Fehler


def AnalysePiezoAngleGratingEdge(shiftstack, shiftDy=0, shiftDx=0):
    """ main function for piezo angle analysis based on grating edge fit"""
    piezo_angle = 0
    num_frames = shiftstack.shape[0]

    shiftstack_rot90CCW = np.rot90(shiftstack, k=1, axes=(1, 2))
    print("Shape of rotated stack is :,", shiftstack_rot90CCW.shape)


    ########Bild Zentrum Zuschneiden
    center_array=[]
    for i in range(num_frames):
        center_array.append(crop_center(shiftstack_rot90CCW[i],400,400, shiftDy, shiftDx))

    #plt.imshow(center_array[0])
    #plt.show()
    # do all the exiting fft frequency and phase stuff for the first frame
    Phasenliste_1, Phasenliste_2, Phasenliste_3, index_i, index_k, index_l,index_m, index_n,index_o = calc_grating_fft_phases_frequencies(shiftstack_rot90CCW[0])

    ##################Bestimmung der Winkel des Gitters aus den 3 Ordnungen##########################
    #################################################################################################  

    AVGWinkel, Winkel_1, Winkel_2, Winkel_3, Winkel_1_Nr2, Winkel_1Mess, WinkelCam_Fehler = calc_grating_angle_from_fft_freq(index_i,index_k, index_l,index_m, index_n,index_o)

    ################## Bestimmung des Verschiebe-Winkel######################################## 
    ###########################################################################################


    ########Bestimmung der Verschiebe Länge in Vertikal-Richtung#############################

    mittelwerte_zeilen0 = np.mean(center_array[0], axis=1)
    mittelwerte_zeilenEnd = np.mean(center_array[num_frames-1], axis=1)


    ######ERF Fit
    #############
    x_data = np.linspace(0, 400, 400)
    y_data = mittelwerte_zeilen0

    #  Fit the model to the data
    params, covariance = curve_fit(erf_model, x_data, y_data, p0=[1, 0.5, 0.5, 0.5])

    # Extract the fitted parameters
    a_fit, b_fit, c_fit, d_fit = params


    PosV0=c_fit/b_fit

    ######################## Plot the data and the fitted curve
    plt.scatter(x_data, y_data, label='Data', color='blue')
    plt.plot(x_data, erf_model(x_data, *params), label=f'Fitted Curve: y = {a_fit:.2f} * erf({b_fit:.2f} * x + {c_fit:.2f}) + {d_fit:.2f}', color='red')
    plt.legend()
    plt.xlabel('x')
    plt.ylabel('y')
    plt.title('Fitting an Error Function Model')
    plt.show()

    # Print the fitted parameters
    print(f"Fitted parameters: a = {a_fit}, b = {b_fit}, c = {c_fit}, d = {d_fit}")

    #############################

    x_data = np.linspace(0, 400, 400)
    y_data = mittelwerte_zeilenEnd

    #   Fit the model to the data
    params, covariance = curve_fit(erf_model, x_data, y_data, p0=[1, 0.5, 0.5, 0.5])

    # Extract the fitted parameters
    a_fit, b_fit, c_fit, d_fit = params


    PosVEnd=c_fit/b_fit


    ######################## Plot the data and the fitted curve
    plt.scatter(x_data, y_data, label='Data', color='blue')
    plt.plot(x_data, erf_model(x_data, *params), label=f'Fitted Curve: y = {a_fit:.2f} * erf({b_fit:.2f} * x + {c_fit:.2f}) + {d_fit:.2f}', color='red')
    plt.legend()
    plt.xlabel('x')
    plt.ylabel('y')
    plt.title('Fitting an Error Function Model')
    plt.show()

    # Print the fitted parameters
    print(f"Fitted parameters: a = {a_fit}, b = {b_fit}, c = {c_fit}, d = {d_fit}")



    #######################Ausgabe des Vertikal Schubes##########################

    SchubV=PosVEnd-PosV0

    print('Schub Vertical')
    print(SchubV)

    ########Bestimmung der Verschiebe Länge in Vertikal-Richtung#############################

    ##Zuschnitt und Verschiebenen der Teilbilder und Export zur Kontrolle

    imTeil1 = Image.fromarray(center_array[0])
    imTeil1.save('Teilbild1.tif')

    imTeil2 = Image.fromarray(center_array[num_frames-1])
    imTeil2.save('Teilbild2.tif')

    center_array_0shift=center_array[0]
    center_array_Endshift=center_array[num_frames-1]

    center_array_0shift=center_array_0shift[(int(SchubV)):]
    center_array_Endshift=center_array_Endshift[:-(int(SchubV))]

    mittelwerte_spalten0 = np.mean(center_array_0shift, axis=0)
    mittelwerte_spaltenEnd = np.mean(center_array_Endshift, axis=0)

    imTeil1 = Image.fromarray(center_array_0shift)
    imTeil1.save('Teilbild1shift.tif')

    imTeil2 = Image.fromarray(center_array_Endshift)
    imTeil2.save('Teilbild2shift.tif')

    #######Fit der Erf des Bildes unten
    x_data = np.linspace(400, 0, 400)
    y_data = mittelwerte_spalten0

    #   Fit the model to the data
    params, covariance = curve_fit(erf_model, x_data, y_data, p0=[1, 0.5, 0.5, 0.5])

    # Extract the fitted parameters
    a_fit, b_fit, c_fit, d_fit = params


    PosH0=c_fit/b_fit

    ######################## Plot the data and the fitted curve
    plt.scatter(x_data, y_data, label='Data', color='blue')
    plt.plot(x_data, erf_model(x_data, *params), label=f'Fitted Curve: y = {a_fit:.2f} * erf({b_fit:.2f} * x + {c_fit:.2f}) + {d_fit:.2f}', color='red')
    plt.legend()
    plt.xlabel('x')
    plt.ylabel('y')
    plt.title('Fitting an Error Function Model')
    plt.show()

    # Print the fitted parameters
    print(f"Fitted parameters: a = {a_fit}, b = {b_fit}, c = {c_fit}, d = {d_fit}")


    #######Fit der Erf des Bildes Oben


    x_data = np.linspace(400, 0, 400)
    y_data = mittelwerte_spaltenEnd

    #   Fit the model to the data
    params, covariance = curve_fit(erf_model, x_data, y_data, p0=[1, 0.5, 0.5, 0.5])

    # Extract the fitted parameters
    a_fit, b_fit, c_fit, d_fit = params


    PosHEnd=c_fit/b_fit
    #############################

    ######################## Plot the data and the fitted curve
    plt.scatter(x_data, y_data, label='Data', color='blue')
    plt.plot(x_data, erf_model(x_data, *params), label=f'Fitted Curve: y = {a_fit:.2f} * erf({b_fit:.2f} * x + {c_fit:.2f}) + {d_fit:.2f}', color='red')
    plt.legend()
    plt.xlabel('x')
    plt.ylabel('y')
    plt.title('Fitting an Error Function Model')
    plt.show()

    # Print the fitted parameters
    print(f"Fitted parameters: a = {a_fit}, b = {b_fit}, c = {c_fit}, d = {d_fit}")


    #########Bestimmung des H-Schubes

    SchubH=PosHEnd-PosH0

    print('Schub Horizontal')
    print(SchubH)

    imTeil = Image.fromarray(center_array[0])
    imTeil.save('Teilbild.tif')

    #############################Bestimmung des Winkels aus H-Schub und V-Schub

    shiftH=[0,SchubH]
    x=[0,SchubV]

    VWinkel=math.degrees(math.atan(SchubH/SchubV))
    VWinkelFehler=-1*(((Winkel_1+ Winkel_1Mess+ Winkel_1_Nr2)/3) - THEORIE_WINKEL_DEG- VWinkel)

    print('Verschieb Vektor zu Kamera in ° (pos im Uhrzeigersin)')
    print(VWinkel)
    print('Verschieb Vektor zu Gitter in ° (pos im Uhrzeigersin)')
    print(VWinkelFehler)

    


    ##########################Plot der Ergebnisse##################################
    ################################################################################

    Plot3c=plt.plot(x, shiftH)

    plt.title(f'Phase Steps Ord-Nr3 und Winkelanalyse \n   \n Theoriewinkel in °:\
 {THEORIE_WINKEL_DEG} \n Gemessener-Winkel Gitter to Cam in ° (aus Ord1-3): {Winkel_1} \n \
               Gemessener-Winkel Gitter to Cam in ° (aus Ord1-1): {Winkel_1Mess}\
                   \n Gemessener-Winkel Gitter to Cam in ° (aus Ord1-2): {Winkel_1_Nr2} \n AVG Gemessener-Winkel Gitter to Cam in °: {AVGWinkel}  \
                   \n Verschiebwinkel in °: {VWinkel} \nVerschiebewinkel zu Gitter Fehler in°: {VWinkelFehler} ')
                  
    plt.xlabel('Vertical Shift in px')
    plt.ylabel('Horizontal Shift in px')
    #plt.text(0, 0, 'Das ist ein Text!',ha='center', va='center', fontsize=12, color='red')
    plt.show()
    piezo_angle = VWinkel
    return piezo_angle, AVGWinkel




########################## PIEZO ANGLE FUNCTION BY FFT PHASE SHIFT ANALYSIS ##################################
    ################################################################################



def AnalysePiezoAngleFFT(shiftstack):
    """ main function for piezo angle analysis based on HR FFT phase shifts"""
    shiftD=5  ##Verschieben des ausgewerteten 2000x2000 Teil des Bildes zu Zentrum in x und y, 
    piezo_angle = 0
    num_frames = shiftstack.shape[0]
    # rotate stack 90 degrees CCW

    shiftstack_rot90CCW = np.rot90(shiftstack, k=1, axes=(1, 2))
    print("Shape of rotated stack is :,", shiftstack_rot90CCW.shape)

    
    Phasenliste_1=np.zeros(num_frames)
    Phasenliste_2=np.zeros(num_frames)
    Phasenliste_3=np.zeros(num_frames)
    

    for z in range(num_frames):
        # do all the exiting fft frequency and phase stuff for each frame
        Phasenliste_1[z], Phasenliste_2[z], Phasenliste_3[z], index_i, index_k, index_l,index_m, index_n,index_o = calc_grating_fft_phases_frequencies(shiftstack_rot90CCW[z])
        


    ##############################
    # debugging tools
    print('Phasenliste_2')
    print(Phasenliste_2)
    #im0 = Image.fromarray(Absolut)
    #im0.save('Spektrum-HR.tif')


    ################# Generate Phase Differences in ym @Gitter and remove 2pi phase jumps

    Phasensteps_1c =np.abs(remove_phase_jumps(Phasenliste_1))*PITCH/(2*np.pi)
    Phasensteps_3c =np.abs(remove_phase_jumps(Phasenliste_3))*PITCH/(2*np.pi)
    Phasensteps_2c =np.abs(remove_phase_jumps(Phasenliste_2))*PITCH/(2*np.pi)
    #Phasensteps_3c =np.abs((Phasenliste_3))*37/(2*np.pi)

    #Phasensteps_3c[0]=Phasensteps_3c[1] ###!!!!! nur zum testen

    Phasensteps_1c_tot=np.sum(Phasensteps_1c)
    Phasensteps_3c_tot=np.sum(Phasensteps_3c)
    Phasensteps_2c_tot=np.sum(Phasensteps_2c)

    x=np.linspace(1, num_frames-1, num_frames-1)
    print('Phasenschritte 2')
    print(Phasensteps_2c)



    Theostep1=(2*np.pi/31)*PITCH/(2*np.pi)
    Theostep3=(12*np.pi/31)*PITCH/(2*np.pi)
    TheoPhase_1=[Theostep1]*(num_frames-1)
    TheoPhase_3=[Theostep3]*(num_frames-1)
    Theostep1_tot=(num_frames-1)*Theostep1
    Theostep3_tot=(num_frames-1)*Theostep3

##################Bestimmung der Winkel des Gitters und der Verschiebung  !!Verschiebe-Vektor Bestimmung unklar!!
    AVGWinkel, Winkel_1, Winkel_2, Winkel_3, Winkel_1_Nr2, Winkel_1Mess, WinkelCam_Fehler = calc_grating_angle_from_fft_freq(index_i,index_k, index_l,index_m, index_n,index_o)
    
    Vektor1=np.array([math.cos(math.radians(Winkel_1)), (-1)*math.sin(math.radians(Winkel_1))])
    Vektor3=np.array([(-1)*math.sin(math.radians(Winkel_3)), (1)*math.cos(math.radians(Winkel_3))])
    Vektor2=np.array([(-1)*math.sin(math.radians(Winkel_2)), (1)*math.cos(math.radians(Winkel_2))])

    


    ###Verschiebevektor Bestimmung aus 1-Nr3 und 1-Nr1

    A = np.array([Vektor1, Vektor3])

    # Rechte Seite B
    B = np.array([(-1)*Phasensteps_1c_tot, Phasensteps_3c_tot])

    # Lösen des Gleichungssystems
    X = np.linalg.solve(A, B)


    V_Winkel=math.degrees(math.atan(X[0]/X[1]))
    V_Winkel_Fehler=-1*(WinkelCam_Fehler-V_Winkel)


    ###Verschiebevektor Bestimmung aus 1-Nr3 und 1-Nr2 !!!!Muss noch korrigiert werden!!

    A2 = np.array([Vektor2, Vektor3])

    # Rechte Seite B
    B2 = np.array([(-1)*Phasensteps_2c_tot, Phasensteps_3c_tot])

    # Lösen des Gleichungssystems
    X2 = np.linalg.solve(A2, B2)


    V_Winkel2=math.degrees(math.atan(X2[0]/X2[1]))
    V_Winkel_Fehler2=-1*(WinkelCam_Fehler-V_Winkel2)

    print('V-Winkel2')
    print(V_Winkel2)

    ################################################

    print('Winkel des Gitters zur Kamera in ° aus Ord1-3:')
    print(Winkel_1)
    print('Winkel des Gitters zur Kamera in ° aus Ord1-1:')
    print(Winkel_1Mess)
    print('Winkel des Gitters zur Kamera Fehler in ° (Positiv im Urzeigersinn):')
    print(WinkelCam_Fehler)
    #print('Gittervektoren Normiert')
    #print(Vektor1)
    #print(Vektor3)
    print('Verschiebevektor:')
    print(X)
    print('VerschiebeWinkelin °:')
    print(V_Winkel)
    print('VerschiebeWinkel zu Gitter Fehler in ° (Positiv im Urzeigersinn):')
    print(V_Winkel_Fehler)
    ##################Plot der Phasenwinkel

    Plot1c=plt.plot(x, Phasensteps_1c)
    Plot1t=plt.plot(x, TheoPhase_1)
    plt.title('Phase Steps Ord-Nr1')
    plt.xlabel('Image Number')
    plt.ylabel('Phase-Step in ym')
    plt.show()

    Plot3c=plt.plot(x, Phasensteps_3c)
    Plot3t=plt.plot(x, TheoPhase_3)
    plt.title(f'Phase Steps Ord-Nr3 und Winkelanalyse \n   \n Theoriewinkel in °:\
    {THEORIE_WINKEL_DEG} \n Gemessener-Winkel Gitter to Cam in ° (aus Ord1-3): {round(Winkel_1, 3)} \n \
               Gemessener-Winkel Gitter to Cam in ° (aus Ord1-1): {round(Winkel_1Mess, 3)}\
                   \n Gemessener-Winkel Gitter to Cam in ° (aus Ord1-2): {round(Winkel_1_Nr2, 3)}\
                  \n VerschiebeWinkel zu Kamera in ° (Positiv im Urzeigersinn): {round(V_Winkel, 3)} \n VerschiebeWinkel zu Gitter Fehler in ° (Positiv im Urzeigersinn): {round(V_Winkel_Fehler, 3)}  ')
    plt.xlabel('Image Number')
    plt.ylabel('Phase-Step in ym')
    #plt.text(0, 0, 'Das ist ein Text!',ha='center', va='center', fontsize=12, color='red')
    plt.savefig(rf"C:\Users\pMACSima-Y-02\Desktop\JustageVorrichtung-Gitterwinkel\Skripts_MZ\Auswertungen\PhaseSteps_OrdNr1_{datetime.datetime.now():%Y%m%d_%H%M%S}.png", dpi=300, bbox_inches='tight')
    plt.show()

    ####Ausgabe der Phasen der Rohbilder, Winkel von Ordnung-3, Verschiebevektor

    #print(l,m)

    #print(Phasenliste_3)

    #print(Winkel_3)
    #print('Winkel 1 Ordnung in Grad')
    #print(Winkel_1)
    #print('Winkel-Fehler Gitter zu Kamera:')
    #print(WinkelCam_Fehler)


    #########Generieren und Exportieren als 32bit float tiff
    #im0 = Image.fromarray(Absolut)
    #im0.save('Spektrum-HR.tif')

    #im1 = Image.fromarray(Ord_3)
    #im1.save('test-SubSpektrumS31.tif')


    # return angle in the end
    piezo_angle = V_Winkel
    return piezo_angle, AVGWinkel
