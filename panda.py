import numpy as np
import matplotlib.pyplot as plt
import pco

with pco.Camera() as cam:
    cam.record()
    image, meta = cam.image()

with pco.Camera() as cam:
    cam.close()

print(meta)
plt.imshow(image)
plt.show()