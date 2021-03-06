from skimage import io, transform
import numpy as np
import matplotlib.pyplot as plt
import keras
from keras import backend as K
K.tensorflow_backend._get_available_gpus()

path = 'data/'

flair = io.imread(path+'flair.png')
t1_v2 = io.imread(path+'t1_v2.png')
t1_v3 = io.imread(path+'t1_v3.png')
t1 = io.imread(path+'t1.png')
t2 = io.imread(path+'t2.png')

'''Noise images'''

plt.subplot(231)
plt.imshow(flair, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3, cmap='gray')
plt.subplot(234)
plt.imshow(t1, cmap='gray')
plt.subplot(235)
plt.imshow(t2, cmap='gray')
plt.tight_layout()


'''Part 1: denoising'''


'''SNR'''

def SNR(a):
    a = np.asanyarray(a)
    m = a.mean()
    sd = a.std()
    return np.where(sd == 0, 0, m/sd)

print(SNR(flair))
print(SNR(t1_v2))
print(SNR(t1_v3))
print(SNR(t1))
print(SNR(t2))


'''1) Bilateral filtering'''

def bilateral(image, sigmaspatial, sigmarange, normed=False,
              samplespatial=None, samplerange=None):

    def fftconvolve3d(image, kernel):
        # fft method
        kerpad = np.zeros_like(image)
        padf = (np.array(image.shape) - np.array(kernel.shape)) // 2
        padb = (np.array(image.shape) + np.array(kernel.shape)) // 2
        kerpad[padf[0]:padb[0], padf[1]:padb[1], padf[2]:padb[2]] = kernel
        
        # Pad the bottom and right side with zeros
        nrows, ncols, ndeps = image.shape
        image = np.pad(image, ((0, nrows-1), (0, ncols-1), (0, ndeps-1)),
                       mode='constant')
        kerpad = np.pad(kerpad, ((0, nrows-1), (0, ncols-1), (0, ndeps-1)),
                        mode='constant')
        cfft = np.fft.ifftn(np.fft.fftn(image) * np.fft.fftn(kerpad))
        row, col, dep = nrows//2, ncols//2, ndeps//2
        
        return cfft.real[row:row+nrows, col:col+ncols, dep:dep+ndeps]
    
    def interp3d(input_array, indices):
        '''Evaluate the input_array data at the indices given'''
    
        output = np.empty(indices[0].shape)
        x_indices, y_indices, z_indices = indices[0], indices[1], indices[2]
        
        x0 = x_indices.astype(np.integer)
        y0 = y_indices.astype(np.integer)
        z0 = z_indices.astype(np.integer)
        x1, y1, z1 = x0 + 1, y0 + 1, z0 + 1
        
        #Check if xyz1 is beyond array boundary:
        x1[np.where(x1==input_array.shape[0])] = x0.max()
        y1[np.where(y1==input_array.shape[1])] = y0.max()
        z1[np.where(z1==input_array.shape[2])] = z0.max()
        
        x, y, z = x_indices - x0, y_indices - y0, z_indices - z0
        output = (input_array[x0,y0,z0]*(1-x)*(1-y)*(1-z) +
                     input_array[x1,y0,z0]*x*(1-y)*(1-z) +
                     input_array[x0,y1,z0]*(1-x)*y*(1-z) +
                     input_array[x0,y0,z1]*(1-x)*(1-y)*z +
                     input_array[x1,y0,z1]*x*(1-y)*z +
                     input_array[x0,y1,z1]*(1-x)*y*z +
                     input_array[x1,y1,z0]*x*y*(1-z) +
                     input_array[x1,y1,z1]*x*y*z)
        
        return output
    
    height = image.shape[0]
    width = image.shape[1]

    samplespatial = sigmaspatial if samplespatial is None else samplespatial
    samplerange = sigmarange if samplerange is None else samplerange

    flatimage = image.flatten()

    edgemin = np.amin(flatimage)
    edgemax = np.amax(flatimage)
    edgedelta = edgemax - edgemin

    derivedspatial = sigmaspatial / samplespatial
    derivedrange = sigmarange / samplerange

    xypadding = np.round(2 * derivedspatial + 1)
    zpadding = np.round(2 * derivedrange + 1)
    
    # allocate 3D grid
    samplewidth = int(np.round((width - 1) / samplespatial) + 1 + 2 * xypadding)
    sampleheight = int(np.round((height - 1) / samplespatial) + 1 + 2 * xypadding)
    sampledepth = int(np.round(edgedelta / samplerange) + 1 + 2 * zpadding)

    dataflat = np.zeros(sampleheight * samplewidth * sampledepth)
    
    # compute downsampled indices
    (ygrid, xgrid) = np.meshgrid(np.arange(width), np.arange(height))

    dimx = np.around(xgrid / samplespatial) + xypadding
    dimy = np.around(ygrid / samplespatial) + xypadding
    dimz = np.around((image - edgemin) / samplerange) + zpadding

    flatx = dimx.flatten()
    flaty = dimy.flatten()
    flatz = dimz.flatten()

    dim = flatz + flaty * sampledepth + flatx * samplewidth * sampledepth
    dim = np.array(dim, dtype=int)

    dataflat[dim] = flatimage

    data = dataflat.reshape(sampleheight, samplewidth, sampledepth)
    
    # make gaussian kernel
    kerneldim = derivedspatial * 2 + 1
    kerneldep = 2 * derivedrange * 2 + 1
    halfkerneldim = np.round(kerneldim / 2)
    halfkerneldep = np.round(kerneldep / 2)

    (gridx, gridy, gridz) = np.meshgrid(np.arange(int(kerneldim)),
                                        np.arange(int(kerneldim)),
                                        np.arange(int(kerneldep)))
    gridx -= int(halfkerneldim)
    gridy -= int(halfkerneldim)
    gridz -= int(halfkerneldep)

    gridsqr = ((gridx * gridx + gridy * gridy) / (derivedspatial * derivedspatial)) \
        + ((gridz * gridz) / (derivedrange * derivedrange))
    kernel = np.exp(-0.5 * gridsqr)

    # convolve
    blurdata = fftconvolve3d(data, kernel)
    
    if normed:
        weights = np.array(data, dtype=bool)
        blurweights = fftconvolve3d(weights, kernel)
        # avoid divide by 0
        blurweights = np.where(blurweights == 0, -2, blurweights)
        blurdata = blurdata / blurweights
        # put 0s where it's undefined
        blurdata = np.where(blurweights < -1, 0, blurdata)
    
    # upsample
    (ygrid, xgrid) = np.meshgrid(np.arange(width), np.arange(height))
    # no rounding
    dimx = (xgrid / samplespatial) + xypadding
    dimy = (ygrid / samplespatial) + xypadding
    dimz = (image - edgemin) / samplerange + zpadding

    return interp3d(blurdata, (dimx, dimy, dimz))


flair_filt = bilateral(flair, 10, 22)
t1_v2_filt = bilateral(t1_v2, 10, 22)
t1_v3_filt = bilateral(t1_v3, 10, 22)
t1_filt = bilateral(t1, 10, 22)
t2_filt = bilateral(t2, 10, 22)


plt.subplot(231)
plt.imshow(flair_filt, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2_filt, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3_filt, cmap='gray')
plt.subplot(234)
plt.imshow(t1_filt, cmap='gray')
plt.subplot(235)
plt.imshow(t2_filt, cmap='gray')
plt.tight_layout()

plt.subplot(231)
plt.imshow(flair-flair_filt, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2-t1_v2_filt, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3-t1_v3_filt, cmap='gray')
plt.subplot(234)
plt.imshow(t1-t1_filt, cmap='gray')
plt.subplot(235)
plt.imshow(t2-t2_filt, cmap='gray')
plt.tight_layout()


'''2) Non-local means'''

def nlmeans(image, f, n):
    def estimate_noise(img):
        upper = img[:-2, 1:-1].flatten()
        lower = img[2:, 1:-1].flatten()
        left = img[1:-1, :-2].flatten()
        right = img[1:-1, 2:].flatten()
        central = img[1:-1, 1:-1].flatten()
        U = np.column_stack((upper, lower, left, right))
        c_estimated = np.dot(U, np.dot(np.linalg.pinv(U), central))
        error = np.mean((central - c_estimated)**2)
        sigma = np.sqrt(error)
        return sigma
    
    # gaussian filter
    def gfunc(x,y,sigma):
        return (np.exp(-(x**2 + y**2)/(2*(sigma**2))))/(2*np.pi*(sigma**2))
    
    def gaussFilter(size, sigma):
        out = np.zeros(size)
        for i in np.arange(size[0]):
            for j in np.arange(size[1]):
                out[i,j] = gfunc(i-size[0]//2,j-size[1]//2, sigma)
        return out/np.sum(out)
    
    def nlmfunc(i, j, fw, fh, nw, nh , image, sigma1, nlmWFilter):
        imgmain = image[i - fh//2:i+1 + fh//2, j - fw//2:j+1 + fw//2, np.newaxis]
        
        nlmFilter = 0
        for p in np.arange(-(nh//2), 1+(nh//2)):
            for q in np.arange(-(nw//2), 1+(nw//2)):
                imgneighbour = image[i + p - fh//2: i+1+p + fh//2,
                                     j+q - fw//2:j+1+q + fw//2, np.newaxis]
                nlmIFilter = ((imgmain - imgneighbour)**2 )/(2*(sigma1**2))
                nlmFilter += np.exp(-1*nlmIFilter)
                
        nlmFilter = nlmFilter/np.sum(nlmFilter,axis=(0,1))
        nlmFilter = nlmFilter*nlmWFilter
        nlmFilter = nlmFilter/np.sum(nlmFilter,axis=(0,1))
        return np.sum(np.multiply(imgmain, nlmFilter),axis=(0,1))
    
    image = np.pad(image, f)
    size = image.shape
    sigma1 = estimate_noise(flair)
    sigma2 = 10
    
    fw, fh, nw, nh = f, f, n, n
    
    nlmWFilter = 2 * np.pi * sigma2**2 * gaussFilter((fw,fh), sigma2)

    if image.ndim < 3:
        nlmWFilter.resize((*nlmWFilter.shape, 1))
    else:
        nlmWFilter = np.stack([nlmWFilter, nlmWFilter, nlmWFilter], axis=2)

    out = np.zeros((size[0]-2*fw +1-nw//2, size[1]-2*fh +1-nh//2, 1))
    for i in np.arange(nh//2, size[0]-2*fh +1-nh//2):
        for j in np.arange(nw//2, size[1]-2*fw +1- nw//2):
            out[i,j,:] = nlmfunc(i+fw-1, j+fh-1, fw, fh, nw, nh, image, sigma1, nlmWFilter)
        
    out[0:nh//2,:,:] = out[nh//2,:,:]
    out[:,0:nw//2,:] = out[:,nw//2,:,np.newaxis]

    out.resize((out.shape[0], out.shape[1]))
    return out

flair_filt = nlmeans(flair, 7, 3)
t1_v2_filt = nlmeans(t1_v2, 7, 3)
t1_v3_filt = nlmeans(t1_v3, 7, 3)
t1_filt = nlmeans(t1, 7, 3)
t2_filt = nlmeans(t2, 7, 3)


plt.subplot(231)
plt.imshow(flair_filt, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2_filt, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3_filt, cmap='gray')
plt.subplot(234)
plt.imshow(t1_filt, cmap='gray')
plt.subplot(235)
plt.imshow(t2_filt, cmap='gray')
plt.tight_layout()

plt.subplot(231)
plt.imshow(flair-flair_filt, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2-t1_v2_filt, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3-t1_v3_filt, cmap='gray')
plt.subplot(234)
plt.imshow(t1-t1_filt, cmap='gray')
plt.subplot(235)
plt.imshow(t2-t2_filt, cmap='gray')
plt.tight_layout()


'''3) Denoising autoencoder'''

def auto(image):
    input_img = keras.layers.Input(shape=image[:, :, np.newaxis].shape)

    x = keras.layers.Conv2D(32, (3, 3), activation='relu', padding='same')(input_img)
    x = keras.layers.MaxPooling2D((2, 2), padding='same')(x)
    x = keras.layers.Conv2D(32, (3, 3), activation='relu', padding='same')(x)
    encoded = keras.layers.MaxPooling2D((2, 2), padding='same')(x)
    
    
    x = keras.layers.Conv2D(32, (3, 3), activation='relu', padding='same')(encoded)
    x = keras.layers.UpSampling2D((2, 2))(x)
    x = keras.layers.Conv2D(32, (3, 3), activation='relu', padding='same')(x)
    x = keras.layers.UpSampling2D((2, 2))(x)
    decoded = keras.layers.Conv2D(1, (3, 3), activation='sigmoid', padding='same')(x)
    
    autoencoder = keras.models.Model(input_img, decoded)
    autoencoder.compile(optimizer='adadelta', loss='binary_crossentropy')
    
    x_train = image.astype('float32') / 255.
    x_train = np.reshape(x_train, (1, image.shape[0], image.shape[1], 1))
    
    autoencoder.fit(x_train, x_train, epochs=50, verbose=0)
    
    decoded_imgs = autoencoder.predict(x_train) * 255
    
    return decoded_imgs.reshape(image.shape)



flair_filt = auto(flair)
t1_v2_filt = auto(t1_v2)
t1_v3_filt = auto(t1_v3)
t1_filt = auto(np.pad(t1, ((0, 0), (1, 1))))
t2_filt = auto(np.pad(t2, ((0, 0), (1, 1))))


plt.subplot(231)
plt.imshow(flair_filt, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2_filt, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3_filt, cmap='gray')
plt.subplot(234)
plt.imshow(t1_filt, cmap='gray')
plt.subplot(235)
plt.imshow(t2_filt, cmap='gray')
plt.tight_layout()

plt.subplot(231)
plt.imshow(flair-flair_filt, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2-t1_v2_filt, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3-t1_v3_filt, cmap='gray')
plt.subplot(234)
plt.imshow(t1-t1_filt[:, 1:-1], cmap='gray')
plt.subplot(235)
plt.imshow(t2-t2_filt[:, 1:-1], cmap='gray')
plt.tight_layout()

'''Part 2: segmentation'''

'''1) Otsu's method'''

def threshold_otsu(image, nbins=256):
    hist, bin_edges = np.histogram(image.ravel(), bins=nbins)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.
    hist = hist.astype(float)

    # class probabilities for all possible thresholds
    weight1 = np.cumsum(hist)
    weight2 = np.cumsum(hist[::-1])[::-1]
    # class means for all possible thresholds
    mean1 = np.cumsum(hist * bin_centers) / weight1
    mean2 = (np.cumsum((hist * bin_centers)[::-1]) / weight2[::-1])[::-1]

    # Clip ends to align class 1 and class 2 variables
    variance12 = weight1[:-1] * weight2[1:] * (mean1[:-1] - mean2[1:]) ** 2

    idx = np.argmax(variance12)
    threshold = bin_centers[:-1][idx]
    return image > threshold

flair_seg = threshold_otsu(flair)
t1_v2_seg = threshold_otsu(t1_v2)
t1_v3_seg = threshold_otsu(t1_v3)
t1_seg = threshold_otsu(t1)
t2_seg = threshold_otsu(t2)


plt.subplot(231)
plt.imshow(flair_seg, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2_seg, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3_seg, cmap='gray')
plt.subplot(234)
plt.imshow(t1_seg, cmap='gray')
plt.subplot(235)
plt.imshow(t2_seg, cmap='gray')
plt.tight_layout()

'''2) Watershed transform'''

class Watershed(object):
    WSHD = 0
    MASK = 1
    INQE = 2

    def __init__(self, levels = 256):
        self.levels = levels

    # Neighbour (coordinates of) pixels, including the given pixel.
    def _get_neighbors(self, height, width, pixel):
        return np.mgrid[np.max(0, pixel[0]-1):np.min(height, pixel[0]+2),
                        np.max(0, pixel[1]-1):np.min(width, pixel[1]+2)].reshape(2, -1).T

    def transforms(self, image):
        current_label = 0
        flag = False
        fifo = []

        height, width = image.shape
        size = height * width
        labels = np.full((height, width), -1, np.int32)

        reshaped_image = image.reshape(size)
        # [y, x] pairs of pixel coordinates of the flattened image.
        pixels = np.mgrid[0:height, 0:width].reshape(2, -1).T
        # Coordinates of neighbour pixels for each pixel.
        neighbours = np.array([self._get_neighbors(height, width, p) for p in pixels])
        if neighbours.ndim == 3:
            # Case where all pixels have the same number of neighbours.
            neighbours = neighbours.reshape(height, width, -1, 2)
        else:
            # Case where pixels may have a different number of pixels.
            neighbours = neighbours.reshape(height, width)

        indices = np.argsort(reshaped_image)
        sorted_image = reshaped_image[indices]
        sorted_pixels = pixels[indices]

        # self.levels evenly spaced steps from minimum to maximum.
        levels = np.linspace(sorted_image[0], sorted_image[-1], self.levels)
        level_indices = []
        current_level = 0

        # Get the indices that deleimit pixels with different values.
        for i in np.arange(size):
            if sorted_image[i] > levels[current_level]:
                # Skip levels until the next highest one is reached.
                while sorted_image[i] > levels[current_level]:
                    current_level += 1
                    level_indices.append(i)
        level_indices.append(size)

        start_index = 0
        for stop_index in level_indices:
            # Mask all pixels at the current level.
            for p in sorted_pixels[start_index:stop_index]:
                labels[p[0], p[1]] = self.MASK
                # Initialize queue with neighbours of existing basins at the current level.
                for q in neighbours[p[0], p[1]]:
                    # p == q is ignored here because labels[p] < WSHD
                    if labels[q[0], q[1]] >= self.WSHD:
                        labels[p[0], p[1]] = self.INQE
                        fifo.append(p)
                        break

            # Extend basins.
            while fifo:
                p = fifo.pop(0)
                # Label p by inspecting neighbours.
                for q in neighbours[p[0], p[1]]:
                    # Don't set lab_p in the outer loop because it may change.
                    lab_p = labels[p[0], p[1]]
                    lab_q = labels[q[0], q[1]]
                    if lab_q > 0:
                        if lab_p == self.INQE or (lab_p == self.WSHD and flag):
                            labels[p[0], p[1]] = lab_q
                        elif lab_p > 0 and lab_p != lab_q:
                            labels[p[0], p[1]] = self.WSHD
                            flag = False
                    elif lab_q == self.WSHD:
                        if lab_p == self.INQE:
                            labels[p[0], p[1]] = self.WSHD
                            flag = True
                    elif lab_q == self.MASK:
                        labels[q[0], q[1]] = self.INQE
                        fifo.append(q)

            # Detect and process new minima at the current level.
            for p in sorted_pixels[start_index:stop_index]:
                # p is inside a new minimum. Create a new label.
                if labels[p[0], p[1]] == self.MASK:
                    current_label += 1
                    fifo.append(p)
                    labels[p[0], p[1]] = current_label
                    while fifo:
                        q = fifo.pop(0)
                        for r in neighbours[q[0], q[1]]:
                            if labels[r[0], r[1]] == self.MASK:
                                fifo.append(r)
                                labels[r[0], r[1]] = current_label

            start_index = stop_index

        return labels

w = Watershed()
flair_seg = w.transforms(flair)
t1_v2_seg = w.transforms(t1_v2)
t1_v3_seg = w.transforms(t1_v3)
t1_seg = w.transforms(t1)
t2_seg = w.transforms(t2)


plt.subplot(231)
plt.imshow(flair_seg, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2_seg, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3_seg, cmap='gray')
plt.subplot(234)
plt.imshow(t1_seg, cmap='gray')
plt.subplot(235)
plt.imshow(t2_seg, cmap='gray')
plt.tight_layout()

'''3) Mean-shift clustering'''


def meanshift(img, h=60, n=10, eps=1e-6):
    H, W = img.shape
    img1 = img.copy()
    img2 = img.copy()
    converaged = np.zeros(img.shape).astype(bool)
    for i in range(n):
        if np.all(converaged > 0):
            break
        for y in range(H):
            for x in range(W):
                if converaged[y, x]:
                    continue
                k = np.square(img1[y, x] - img1) / h
                g = np.exp(-k**2 / 2)
                d = ((img1 - img1[y, x]) / h * g)
                m = ((img1 * d).sum() / d.sum() - img1[y, x]) / h**2 * g.sum()
                f = d.sum() / h / H / W / g.sum()
                grad = m * f / k.sum()
                if grad < eps:
                    converaged[y, x] = True
                    continue
                img2[y, x] += m
        img1 = img2
    return img1
    

flair_seg = meanshift(flair)
t1_v2_seg = meanshift(t1_v2)
t1_v3_seg = meanshift(t1_v3)
t1_seg = meanshift(t1)
t2_seg = meanshift(t2)


plt.subplot(231)
plt.imshow(flair_seg, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2_seg, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3_seg, cmap='gray')
plt.subplot(234)
plt.imshow(t1_seg, cmap='gray')
plt.subplot(235)
plt.imshow(t2_seg, cmap='gray')
plt.tight_layout()

'''4) Graph cut'''

def ncut(im, n_splits=1, split_type='mean'):
    sz = np.prod(im.shape)
    ind = np.arange(sz)
    
    def ind2sub(array_shape, ind):
        # Gives repeated indices, replicates matlabs ind2sub
        rows = (ind.astype('int32') // array_shape[1])
        cols = (ind.astype('int32') % array_shape[1])
        return (rows, cols)
    
    I, J = ind2sub(im.shape, ind)
    I = I + 1
    J = J + 1
    
    scaling = 255.
    scaled_im = im.ravel() / float(scaling)
    
    # float32 gives the wrong answer...
    scaled_im = scaled_im.astype('float64')
    sim = np.zeros((sz, sz)).astype('float64')
    
    rad = 5
    sigma_x = .3
    sigma_p = .1
    
    # Faster with broadcast tricks
    x1 = I[None]
    x2 = I[:, None]
    y1 = J[None]
    y2 = J[:, None]
    dist = (x1 - x2) ** 2 + (y1 - y2) ** 2
    scale = np.exp(-(dist / (sigma_x ** 2)))
    sim = scale
    sim[np.sqrt(dist) >= rad] = 0.
    del x1; del x2; del y1; del y2; del dist;
    
    p1 = scaled_im[None]
    p2 = scaled_im[:, None]
    pdist = (p1 - p2) ** 2
    pscale = np.exp(-(pdist / (sigma_p ** 2)))
    
    sim *= pscale
    
    dind = np.diag_indices_from(sim)
    sim[dind] = 1.
    
    d = np.sum(sim, axis=1)
    D = np.diag(d)
    A = (D - sim)
    
    # Cholesky factorization
    L = np.linalg.cholesky(D)
    L_inv = np.linalg.inv(L)
    C = L_inv @ A @ L_inv.T.conj()

    # Want second smallest eigenvector onward
    S, V = np.linalg.eigh(C)
    S = S[1:n_splits+2]
    V = (L_inv.T.conj() @ V)[:, 1:n_splits+2]
    sort_ind = np.argsort(S)
    S = S[sort_ind]
    V = V[:, sort_ind]
    segs = V
    segs[:, -1] = ind
    
    
    def cut(im, matches, ix, split_type=split_type):
        # Can choose how to split
        if split_type == 'mean':
            split = np.mean(segs[:, ix])
        elif split_type == 'median':
            split = np.median(segs[:, ix])
        elif split_type == 'zero':
            split = 0.
        else:
            raise ValueError('Unknown split type %s' % split_type)
    
        meets = np.where(matches[:, ix] >= split)[0]
        match1 = matches[meets, :]
        res1 = np.zeros_like(im)
        match_inds = match1[:, -1].astype('int32')
        res1.ravel()[match_inds] = im.ravel()[match_inds]
    
        meets = np.where(matches[:, ix] < split)[0]
        match2 = matches[meets, :]
        res2 = np.zeros_like(im)
        match_inds = match2[:, -1].astype('int32')
        res2.ravel()[match_inds] = im.ravel()[match_inds]
        return [match1, match2], [res1, res2]
    
    # Recursively split partition and stores intermediates
    all_splits = []
    all_matches = [[segs]]
    for i in np.arange(n_splits):
        matched = all_matches[-1]
        current_splits = []
        current_matches = []
        for s in matched:
            matches, splits = cut(im, s, i)
            current_splits.extend(splits)
            current_matches.extend(matches)
        all_splits.append(current_splits)
        all_matches.append(current_matches)
        
    return all_splits[-1]

flair_re = transform.rescale(flair_filt, 0.1)
t1_v2_re = transform.rescale(t1_v2_filt, 0.2)
t1_v3_re = transform.rescale(t1_v3_filt, 0.2)
t1_re = transform.rescale(t1_filt, 0.15)
t2_re = transform.rescale(t2_filt, 0.15)


to_plot = ncut(flair_re, split_type='zero')
for i in np.arange(len(to_plot)):
    plt.subplot(1, len(to_plot), i+1)
    plt.imshow(to_plot[i], cmap='gray')
plt.tight_layout()

to_plot = ncut(t1_v2_re, split_type='median')
for i in np.arange(len(to_plot)):
    plt.subplot(1, len(to_plot), i+1)
    plt.imshow(to_plot[i], cmap='gray')
plt.tight_layout()

to_plot = ncut(t1_v3_re, split_type='median')
for i in np.arange(len(to_plot)):
    plt.subplot(1, len(to_plot), i+1)
    plt.imshow(to_plot[i], cmap='gray')
plt.tight_layout()

to_plot = ncut(t1_re, split_type='median')
for i in np.arange(len(to_plot)):
    plt.subplot(1, len(to_plot), i+1)
    plt.imshow(to_plot[i], cmap='gray')
plt.tight_layout()

to_plot = ncut(t2_re, split_type='median')
for i in np.arange(len(to_plot)):
    plt.subplot(1, len(to_plot), i+1)
    plt.imshow(to_plot[i], cmap='gray')
plt.tight_layout()

'''5) Neural network'''

class SOM(object):
    def __init__(self, x, y, input_len, sigma=1.0, learning_rate=0.5,
                 random_seed=None):
        if sigma >= x or sigma >= y:
            raise ValueError('sigma is too high for the dimension of the map.')

        self._random_generator = np.random.RandomState(random_seed)

        self._learning_rate = learning_rate
        self._sigma = sigma
        self._input_len = input_len
        # random initialization
        self._weights = self._random_generator.rand(x, y, input_len)*2-1
        self._weights /= np.linalg.norm(self._weights, axis=-1, keepdims=True)

        self._activation_map = np.zeros((x, y))
        self._neigx = np.arange(x)
        self._neigy = np.arange(y)  # used to evaluate the neighborhood function
        
        self._xx, self._yy = np.meshgrid(self._neigx, self._neigy)
        self._xx = self._xx.astype(float)
        self._yy = self._yy.astype(float)
        
        self._decay_function = self._asymptotic_decay
        
        self._neighborhood = self._gaussian
        
        self._activation_distance = self._euclidean_distance
    
    def _build_iteration_indexes(self, data_len, num_iterations,
                                 random_generator=None):
        iterations = np.arange(num_iterations) % data_len
        if random_generator:
            random_generator.shuffle(iterations)
        return iterations
        
    def _asymptotic_decay(self, learning_rate, t, max_iter):
        return learning_rate / (1+t/(max_iter/2))
    
    def _activate(self, x):
        '''Updates matrix activation_map, in this matrix
        the element i,j is the response of the neuron i,j to x.'''
        self._activation_map = self._activation_distance(x, self._weights)
    
    def _gaussian(self, c, sigma):
        '''Returns a Gaussian centered in c.'''
        d = 2*np.pi*sigma*sigma
        ax = np.exp(-np.power(self._xx-self._xx.T[c], 2)/d)
        ay = np.exp(-np.power(self._yy-self._yy.T[c], 2)/d)
        return (ax * ay).T  # the external product gives a matrix
    
    def _euclidean_distance(self, x, w):
        return np.linalg.norm(np.subtract(x, w), axis=-1)
    
    def _check_input_len(self, data):
        '''Checks that the data in input is of the correct shape.'''
        data_len = len(data[0])
        if self._input_len != data_len:
            msg = 'Received %d features, expected %d.' % (data_len,
                                                          self._input_len)
            raise ValueError(msg)
            
    def _check_iteration_number(self, num_iteration):
        if num_iteration < 1:
            raise ValueError('num_iteration must be > 1')
            
    def _quantization(self, data):
        '''Assigns a code book (weights vector of the winning neuron)
        to each sample in data.'''
        self._check_input_len(data)
        winners_coords = np.argmin(self._distance_from_weights(data), axis=1)
        return self._weights[np.unravel_index(winners_coords,
                                              self._weights.shape[:2])]
    
    def _random_weights_init(self, data):
        '''Initializes the weights of the SOM picking random samples from data.'''
        self._check_input_len(data)
        it = np.nditer(self._activation_map, flags=['multi_index'])
        while not it.finished:
            rand_i = self._random_generator.randint(len(data))
            self._weights[it.multi_index] = data[rand_i]
            it.iternext()
            
    def _winner(self, x):
        '''Computes the coordinates of the winning neuron for the sample x.'''
        self._activate(x)
        return np.unravel_index(self._activation_map.argmin(),
                                self._activation_map.shape)
            
    def _update(self, x, win, t, max_iteration):
        eta = self._decay_function(self._learning_rate, t, max_iteration)
        # sigma and learning rate decrease with the same rule
        sig = self._decay_function(self._sigma, t, max_iteration)
        # improves the performances
        g = self._neighborhood(win, sig)*eta
        # w_new = eta * neighborhood_function * (x-w)
        self._weights += np.einsum('ij, ijk->ijk', g, x-self._weights)
            
    def _train(self, data, num_iteration, random_order=False):
        self._check_iteration_number(num_iteration)
        self._check_input_len(data)
        random_generator = None
        if random_order:
            random_generator = self._random_generator
        iterations = self._build_iteration_indexes(len(data), num_iteration,
                                                   random_generator)
        for t, iteration in enumerate(iterations):
            self._update(data[iteration], self._winner(data[iteration]),
                        t, num_iteration)
            
    def _train_random(self, data, num_iteration):
        self._train(data, num_iteration, random_order=True)
        
    def _distance_from_weights(self, data):
        '''Returns a matrix d where d[i,j] is the euclidean distance between
        data[i] and the j-th weight.'''
        input_data = np.array(data)
        weights_flat = self._weights.reshape(-1, self._weights.shape[2])
        input_data_sq = np.power(input_data, 2).sum(axis=1, keepdims=True)
        weights_flat_sq = np.power(weights_flat, 2).sum(axis=1, keepdims=True)
        cross_term = np.dot(input_data, weights_flat.T)
        return np.sqrt(np.abs(-2 * cross_term + input_data_sq + weights_flat_sq.T))
    
    def cluster(self, image):
        data = image.ravel()
        if data.ndim < 2:
            data = data[:, np.newaxis]
        self._random_weights_init(data)
        self._train_random(data, 100)
        qnt = self._quantization(data)
        return qnt.reshape(image.shape)
    
som = SOM(x=3, y=3, input_len=1, sigma=0.1, learning_rate=0.2)

flair_seg = som.cluster(flair)
t1_v2_seg = som.cluster(t1_v2)
t1_v3_seg = som.cluster(t1_v3)
t1_seg = som.cluster(t1)
t2_seg = som.cluster(t2)

flair_seg = som.cluster(flair_filt)
t1_v2_seg = som.cluster(t1_v2_filt)
t1_v3_seg = som.cluster(t1_v3_filt)
t1_seg = som.cluster(t1_filt)
t2_seg = som.cluster(t2_filt)

plt.subplot(231)
plt.imshow(flair_seg, cmap='gray')
plt.subplot(232)
plt.imshow(t1_v2_seg, cmap='gray')
plt.subplot(233)
plt.imshow(t1_v3_seg, cmap='gray')
plt.subplot(234)
plt.imshow(t1_seg, cmap='gray')
plt.subplot(235)
plt.imshow(t2_seg, cmap='gray')
plt.tight_layout()


'''Part 3: vascular segmentation'''

import nibabel as nib

path = 'anats/'


'''arteries from tof'''

fname = 'tof_in_swi_brain_susan'
tof = nib.load(path+fname+'.nii.gz')
img = tof.get_fdata()


img[img < 230] = 0
img[img != 0] = 1

nimg = nib.Nifti1Image(img, tof.affine)
nib.save(nimg, path+fname+'_thres.nii.gz')


'''veins from swi'''

fname = 'swi_brain_susan'
swi = nib.load(path+fname+'.nii.gz')
img = swi.get_fdata()

mask = nib.load(path+'swi_brain_mask.nii.gz')
msk = mask.get_fdata()

res = (img.max() - img) * msk
nimg = nib.Nifti1Image(res, swi.affine)
nib.save(nimg, path+fname+'_inv.nii.gz')

res[res > 110] = 1
res[res != 1] = 0

nimg = nib.Nifti1Image(res, swi.affine)
nib.save(nimg, path+fname+'_thres.nii.gz')

'''kimimaro skeletonization'''

import kimimaro

skels = kimimaro.skeletonize(img)
skel = skels[1]
skel.viewer()

skels = kimimaro.skeletonize(res)
skel = skels[1]
skel.viewer()
