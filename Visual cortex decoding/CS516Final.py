import numpy as np
import pandas as pd
import nibabel as nib
from skimage.io import imread
from skimage.transform import resize

# 3d prepresentation
path = 'final/sub-01/'

samples = []
imgs = []

for i in [1]:
    for j in [10, 12]:
        folder = 'perceptionLetterImage'
        file = ('sub-01_ses-' + folder + '0' + str(i) +'_func_' +
                'sub-01_ses-' + folder + '0' + str(i) +
                '_task-perception_run-' + str(j) + '_')
        
        norm_clean = nib.load(path + folder + '/norm_clean_reg_mc_despike_'+
                              file + 'bold.nii.gz')
        tr = norm_clean.header.get_zooms()[-1]
        img = norm_clean.get_fdata()
        events = pd.read_csv(path + folder + '/' + file + 'events.tsv', delimiter='\t')
        
        start_indices = ((events['onset'][~events['stimulus_name'].isna()].values + 4) // tr).astype(int)
        end_indices = ((events['onset'][~events['stimulus_name'].isna()].values + 4 +
                        events['duration'][~events['stimulus_name'].isna()].values) // tr).astype(int)
        for k in range(len(start_indices)):
            sample = img[:, :, :, start_indices[k]:end_indices[k]].mean(-1)
            # trim brain
            l, w, h = sample.shape
            begin = (l - 80) // 2
            end = l - 80 - begin
            sample = sample[begin:end, w // 3 * 2:, :]
            pad_h = (l - h) // 2
            sample = np.pad(sample, ((0, 0), (0, 0), (pad_h, l - h - pad_h)))
            samples += [sample]
        
        names = events['stimulus_name'][~events['stimulus_name'].isna()].values
        imgs += list(names)
        
# average duplicates
samples = np.array(samples)
imgs = np.array(imgs)

img_folder = 'images/'

img_type = 'training'
img_ids = pd.read_csv(path + img_folder + 'image_' + img_type + '_id.csv', header=None)[1].values

avg_samples = []
gray_imgs = []
for img_id in img_ids:
    
    curr_id = img_id.split('.')[0]
    avg_sample = samples[imgs == curr_id].mean(0)
    avg_sample = np.expand_dims(avg_sample, -1)
    avg_samples += [avg_sample]
    
    
    curr_img = imread(path + img_folder + img_type + '/' + img_id, as_gray=True)
    curr_img = resize(curr_img, (80, 80))
    gray_imgs += [curr_img]
    
np.savez_compressed(path + img_type + '_samples.npz', np.array(avg_samples))
np.savez_compressed(path + img_type + '_imgs.npz', np.array(gray_imgs))


# Load training data
files = ['training', 'test', 'ArtificialImage', 'LetterImage']

x_train = []
y_train = []

for file in files:
    x_train += list(np.load(path + file + '_samples.npz')['arr_0'])
    y_train += list(np.load(path + file + '_imgs.npz')['arr_0'])

# To avoid 'float64' error in Tensor
x_train = np.array(x_train).astype('float32')
y_train = np.array(y_train).astype('float32')
# Normalize the images to [-1, 1]
y_train = (y_train - 0.5) / 0.5


# U-shape network
import time
import matplotlib.pyplot as plt
import tensorflow as tf
import tensorflow_addons as tfa
import tensorflow_probability as tfp
from tensorflow.python.keras import backend as K
tf.config.experimental.list_physical_devices('GPU')
K.set_image_data_format('channels_last')

def _bernoulli(shape, mean):
    return tf.nn.relu(tf.sign(mean - tf.random_uniform(shape, minval=0, maxval=1, dtype=tf.float32)))

# DropBlock
class DropBlock2D(tf.keras.layers.Layer):
    def __init__(self, keep_prob, block_size, scale=True, **kwargs):
        super(DropBlock2D, self).__init__(**kwargs)
        self.keep_prob = float(keep_prob) if isinstance(keep_prob, int) else keep_prob
        self.block_size = int(block_size)
        self.scale = tf.constant(scale, dtype=tf.bool) if isinstance(scale, bool) else scale

    def compute_output_shape(self, input_shape):
        return input_shape

    def build(self, input_shape):
        assert len(input_shape) == 4
        _, self.h, self.w, self.channel = input_shape.as_list()
        # pad the mask
        p1 = (self.block_size - 1) // 2
        p0 = (self.block_size - 1) - p1
        self.padding = [[0, 0], [p0, p1], [p0, p1], [0, 0]]
        self.set_keep_prob()
        super(DropBlock2D, self).build(input_shape)

    def call(self, inputs, training=None, **kwargs):
        def drop():
            mask = self._create_mask(tf.shape(inputs))
            output = inputs * mask
            output = tf.cond(self.scale,
                             true_fn=lambda: output * tf.to_float(tf.size(mask)) / tf.reduce_sum(mask),
                             false_fn=lambda: output)
            return output

        if training is None:
            training = K.learning_phase()
        output = tf.cond(tf.logical_or(tf.logical_not(training), tf.equal(self.keep_prob, 1.0)),
                         true_fn=lambda: inputs,
                         false_fn=drop)
        return output

    def set_keep_prob(self, keep_prob=None):
        """This method only supports Eager Execution"""
        if keep_prob is not None:
            self.keep_prob = keep_prob
        w, h = tf.to_float(self.w), tf.to_float(self.h)
        self.gamma = (1. - self.keep_prob) * (w * h) / (self.block_size ** 2) / \
                     ((w - self.block_size + 1) * (h - self.block_size + 1))

    def _create_mask(self, input_shape):
        sampling_mask_shape = tf.stack([input_shape[0],
                                       self.h - self.block_size + 1,
                                       self.w - self.block_size + 1,
                                       self.channel])
        mask = _bernoulli(sampling_mask_shape, self.gamma)
        mask = tf.pad(mask, self.padding)
        mask = tf.nn.max_pool(mask, [1, self.block_size, self.block_size, 1], [1, 1, 1, 1], 'SAME')
        mask = 1 - mask
        return mask

class DropBlock3D(tf.keras.layers.Layer):
    def __init__(self, keep_prob, block_size, scale=True, **kwargs):
        super(DropBlock3D, self).__init__(**kwargs)
        self.keep_prob = float(keep_prob) if isinstance(keep_prob, int) else keep_prob
        self.block_size = int(block_size)
        self.scale = tf.constant(scale, dtype=tf.bool) if isinstance(scale, bool) else scale

    def compute_output_shape(self, input_shape):
        return input_shape

    def build(self, input_shape):
        assert len(input_shape) == 5
        _, self.d, self.h, self.w, self.channel = input_shape.as_list()
        # pad the mask
        p1 = (self.block_size - 1) // 2
        p0= (self.block_size - 1) - p1
        self.padding = [[0, 0], [p0, p1], [p0, p1], [p0, p1], [0, 0]]
        self.set_keep_prob()
        super(DropBlock3D, self).build(input_shape)

    def call(self, inputs, training=None, **kwargs):
        def drop():
            mask = self._create_mask(tf.shape(inputs))
            output = inputs * mask
            output = tf.cond(self.scale,
                             true_fn=lambda: output * tf.to_float(tf.size(mask)) / tf.reduce_sum(mask),
                             false_fn=lambda: output)
            return output

        if training is None:
            training = K.learning_phase()
        output = tf.cond(tf.logical_or(tf.logical_not(training), tf.equal(self.keep_prob, 1.0)),
                         true_fn=lambda: inputs,
                         false_fn=drop)
        return output

    def set_keep_prob(self, keep_prob=None):
        """This method only supports Eager Execution"""
        if keep_prob is not None:
            self.keep_prob = keep_prob
        d, w, h = tf.to_float(self.d), tf.to_float(self.w), tf.to_float(self.h)
        self.gamma = ((1. - self.keep_prob) * (d * w * h) / (self.block_size ** 3) /
                      ((d - self.block_size + 1) * (w - self.block_size + 1) * (h - self.block_size + 1)))

    def _create_mask(self, input_shape):
        sampling_mask_shape = tf.stack([input_shape[0],
                                        self.d - self.block_size + 1,
                                        self.h - self.block_size + 1,
                                        self.w - self.block_size + 1,
                                        self.channel])
        mask = _bernoulli(sampling_mask_shape, self.gamma)
        mask = tf.pad(mask, self.padding)
        mask = tf.nn.max_pool3d(mask, [1, self.block_size, self.block_size, self.block_size, 1], [1, 1, 1, 1, 1], 'SAME')
        mask = 1 - mask
        return mask

def unet(input_shape, regularization=None, regularization_parameters=None, deconvolution=False):
    depth = 5
    channels = 1
    kernel_regularizer = None
    if regularization is not None:
        if regularization == 'l2':
            kernel_regularizer = tf.keras.regularizers.l2(*regularization_parameters)
        elif regularization == 'l1':
            kernel_regularizer = tf.keras.regularizers.l1(*regularization_parameters)
        elif regularization == 'l1_l2':
            kernel_regularizer = tf.keras.regularizers.l1_l2(*regularization_parameters)

    def _add_regularization_layer(input_layer, name_suffix, input_type='2d', activation='relu'):

        regularization_layer = None

        if regularization == 'batch_norm':
            layer_name = name_suffix + '_Batch_Norm'
            regularization_layer = tf.keras.layers.BatchNormalization(-1, momentum=0.8, name=layer_name)(input_layer)

        elif regularization == 'instance_norm':
            layer_name = name_suffix + '_Instance_Norm'
            regularization_layer = tfa.layers.InstanceNormalization(-1, name=layer_name)(input_layer)

        elif regularization == 'dropout':
            layer_name = name_suffix + '_Dropout'
            regularization_layer = tf.keras.layers.Dropout(*regularization_parameters, name=layer_name)(input_layer)

        elif regularization == 'dropblock':
            layer_name = name_suffix + '_DropBlock'
            if input_type == '1d':
                return input_layer
            elif input_type == '2d':
                regularization_layer = DropBlock2D(*regularization_parameters, name=layer_name)(input_layer)
            elif input_type == '3d':
                regularization_layer = DropBlock3D(*regularization_parameters, name=layer_name)(input_layer)
        layer_name =  name_suffix + '_Activation'
        if regularization_layer is not None:
            if activation == 'leaky_relu':
                output = tf.keras.layers.LeakyReLU(alpha=0.2, name=layer_name)(regularization_layer)
            else:
                output = tf.keras.layers.Activation(activation=activation, name=layer_name)(regularization_layer)
        else:
            if activation == 'leaky_relu':
                output = tf.keras.layers.LeakyReLU(alpha=0.2, name=layer_name)(input_layer)
            else:
                output = tf.keras.layers.Activation(activation=activation, name=layer_name)(input_layer)
        return output

    def _get_convolution3D_block(input_layer, filters, kernel_size=3, strides=1, padding='same',
                                 name_prefix='l_', activation='relu'):
        conv3d_layer_name_1 = name_prefix + "Conv3D_{}_1".format(int(filters))
        conv3d_1 = tf.keras.layers.Conv3D(filters=filters, kernel_size=kernel_size, strides=strides, padding=padding,
                                          name=conv3d_layer_name_1,
                                          kernel_regularizer=kernel_regularizer)

        conv3d_1 = conv3d_1(input_layer)
        conv3d_1 = _add_regularization_layer(conv3d_1, name_suffix=conv3d_layer_name_1,
                                             input_type='3d', activation=activation)
        conv3d_layer_name_2 = name_prefix + "Conv3D_{}_2".format(int(filters))
        conv3d_2 = tf.keras.layers.Conv3D(filters=filters, kernel_size=kernel_size, strides=strides, padding=padding,
                                          name=conv3d_layer_name_2,
                                          kernel_regularizer=kernel_regularizer)

        conv3d_2 = conv3d_2(conv3d_1)
        conv3d_2 = _add_regularization_layer(conv3d_2, name_suffix=conv3d_layer_name_2, 
                                             input_type='3d', activation=activation)

        return conv3d_2

    def _get_convolution_block(input_layer, filters, kernel_size=3, strides=1, padding='same',
                               name_prefix='l_', activation='relu'):

        in_b, in_w, in_h, in_t, in_c = input_layer.get_shape().as_list()
        permute_layer_name_1 = name_prefix + "Permute_{}_1".format(int(filters))
        permute_layer_1 = tf.keras.layers.Permute((2, 1, 3, 4), name=permute_layer_name_1)

        permute_layer_1 = permute_layer_1(input_layer)
        reshape_layer_name_1 = name_prefix + "Reshape_{}_1".format(int(filters))
        reshape_layer_1 = tf.reshape(permute_layer_1, shape=(-1, in_w, in_t, in_c), name=reshape_layer_name_1)

        conv2d_layer_name_1 = name_prefix + "Conv2D_{}_1".format(int(filters))
        conv2d_1 = tf.keras.layers.Conv2D(filters=filters, kernel_size=kernel_size, strides=strides,
                                          padding=padding, name=conv2d_layer_name_1,
                                          kernel_regularizer=kernel_regularizer)

        conv2d_1 = conv2d_1(reshape_layer_1)
        conv2d_1 = _add_regularization_layer(conv2d_1, name_suffix=conv2d_layer_name_1, activation=activation)
        reshape_layer_name_2 = name_prefix + "Reshape_{}_2".format(int(filters))
        reshape_layer_2 = tf.reshape(conv2d_1, shape=(-1, in_h, in_w, in_t, filters), name=reshape_layer_name_2)

        permute_layer_name_2 = name_prefix + "Permute_{}_2".format(int(filters))
        permute_layer_2 = tf.keras.layers.Permute((2, 1, 3, 4), name=permute_layer_name_2)
        permute_layer_2 = permute_layer_2(reshape_layer_2)
        reshape_layer_name_3 = name_prefix + "Reshape_{}_3".format(int(filters))
        reshape_layer_3 = tf.reshape(permute_layer_2, shape=(-1, in_h, in_t, filters), name=reshape_layer_name_3)
        conv2d_layer_name_2 = name_prefix + "Conv2D_{}_2".format(int(filters))
        conv2d_2 = tf.keras.layers.Conv2D(filters=filters, kernel_size=kernel_size, strides=strides,
                                          padding=padding, name=conv2d_layer_name_2,
                                          kernel_regularizer=kernel_regularizer)

        conv2d_2 = conv2d_2(reshape_layer_3)
        conv2d_2 = _add_regularization_layer(conv2d_2, name_suffix=conv2d_layer_name_2, activation=activation)
        reshape_layer_name_4 = name_prefix + "Reshape_{}_4".format(int(filters))
        reshape_layer_4 = tf.reshape(conv2d_2, shape=(-1, in_w, in_h, in_t, filters), name=reshape_layer_name_4)

        return reshape_layer_4

    def _get_convolution2D_block(input_layer, filters, kernel_size=3, strides=1, padding='same',
                                 name_prefix='l_', activation='relu'):
        conv2d_layer_name_1 = name_prefix + "Conv2D_{}_1".format(int(filters))
        conv2d_1 = tf.keras.layers.Conv2D(filters=filters, kernel_size=kernel_size, strides=strides, padding=padding,
                                          name=conv2d_layer_name_1,
                                          kernel_regularizer=kernel_regularizer)

        conv2d_1 = conv2d_1(input_layer)
        conv2d_1 = _add_regularization_layer(conv2d_1, name_suffix=conv2d_layer_name_1,
                                             input_type='2d', activation=activation)

        conv2d_layer_name_2 = name_prefix + "Conv2D_{}_2".format(int(filters))
        conv2d_2 = tf.keras.layers.Conv2D(filters=filters, kernel_size=kernel_size, strides=strides, padding=padding,
                                          name=conv2d_layer_name_2,
                                          kernel_regularizer=kernel_regularizer)

        conv2d_2 = conv2d_2(conv2d_1)

        return conv2d_2

    def _get_up_convolution2D_layer(input_layer, filters, kernel_size=3, strides=2, padding='same',
                                           name_prefix='r_', activation='relu', use_bias=True, deconvolution=deconvolution):
        conv2d_up_layer_name = name_prefix + "UpConv2D_{}".format(int(filters))
        if deconvolution:
            conv2d_up = tf.keras.layers.Convolution2DTranspose(filters=filters, kernel_size=kernel_size,
                                                               strides=strides, padding=padding,
                                                               name=conv2d_up_layer_name, use_bias=use_bias,
                                                               kernel_regularizer=kernel_regularizer)
        else:
            conv2d_up = tf.keras.layers.UpSampling2D(size=(2, 2), name=conv2d_up_layer_name)
        conv2d_up = conv2d_up(input_layer)
        conv2d_up = _add_regularization_layer(conv2d_up, name_suffix=conv2d_up_layer_name, input_type='2d', activation=activation)
        return conv2d_up

    def _get_max_pool_3d_layer(filters, pool_size=(2, 2, 2), padding='same', name_prefix='l_'):
        maxpool_3d_layer_name = name_prefix + "MaxPool3D_{}".format(int(filters))
        maxpool_3d = tf.keras.layers.MaxPooling3D(pool_size=pool_size, padding=padding, name=maxpool_3d_layer_name)
        return maxpool_3d

    base_filters = 32
    inputs = tf.keras.Input(input_shape, name='Input', dtype='float32')
    current_layer = tf.expand_dims(inputs, -1, name='ChannelExpand')

    for d in range(depth):
        filters = base_filters * 2 ** d
        first_layer = _get_convolution_block(input_layer=current_layer, filters=filters)
        if d < depth - 1:
            current_layer = _get_max_pool_3d_layer(filters=filters)(first_layer)
        else:
            current_layer = first_layer

    current_layer = tf.math.reduce_max(current_layer, 2, name='ReduceMax')

    for d in range(depth - 2, -1, -1):
        filters = base_filters * 2 ** d
        up_convolution_layer = _get_up_convolution2D_layer(input_layer=current_layer, filters=filters,
                                                           kernel_size=5, activation='leaky_relu', use_bias=False)
        current_layer = _get_convolution2D_block(input_layer=up_convolution_layer, filters=filters, name_prefix='r_')

    deconv2d_1_1 = tf.keras.layers.Conv2DTranspose(filters=channels, kernel_size=5, strides=1, padding='same',
                                                   name='deconv2d_1_1', kernel_regularizer=kernel_regularizer,
                                                   use_bias=False, activation='tanh')
    deconv2d_1_1 = deconv2d_1_1(current_layer)
    deconv2d_1_1 = _add_regularization_layer(deconv2d_1_1, name_suffix='deconv2d_1_1',
                                             input_type='2d', activation='tanh')

    outputs = tf.squeeze(deconv2d_1_1, -1, name='ChannelSqueeze')
    return tf.keras.Model(inputs=inputs, outputs=outputs, name='UNet')

gen = unet(tuple(np.array(x_train.shape)[1:]), regularization='batch_norm')

# Network diagram
gen.summary()
tf.keras.utils.plot_model(gen, show_shapes=True)

# Training
gen.compile(optimizer='adam', loss='mse')
gen.fit(x_train, y_train, epochs=250)

gen.save('model')

# Prediction
pred = gen.predict(x_train)


# Plot natural images
num = 4
idx = np.random.randint(len(y_train)-50, size=num)
f, axs = plt.subplots(2, num)
for i in np.arange(0, num):
    axs[0, i].imshow(y_train[idx[i]], cmap='gray')
    axs[0, i].axis('off')
    axs[1, i].imshow(pred[idx[i]], cmap='gray')
    axs[1, i].axis('off')
plt.tight_layout()

# Plot artificial images
idx = np.random.randint(len(y_train)-50, len(y_train)-10, size=num)
f, axs = plt.subplots(2, num)
for i in np.arange(0, num):
    axs[0, i].imshow(y_train[idx[i]], cmap='gray')
    axs[0, i].axis('off')
    axs[1, i].imshow(pred[idx[i]], cmap='gray')
    axs[1, i].axis('off')
plt.tight_layout()

# Plot letter images
idx = np.random.randint(len(y_train)-10, len(y_train), size=num)
f, axs = plt.subplots(2, num)
for i in np.arange(0, num):
    axs[0, i].imshow(y_train[idx[i]], cmap='gray')
    axs[0, i].axis('off')
    axs[1, i].imshow(pred[idx[i]], cmap='gray')
    axs[1, i].axis('off')
plt.tight_layout()

# Plot pearson correlation
res = tfp.stats.correlation(tf.reshape(y_train, (len(y_train), -1)), tf.reshape(pred, (len(pred), -1)), sample_axis=-1, event_axis=None)
img_type = ['Natural', 'Artificial', 'Letter']
prs = [np.mean(res[:-50]), np.mean(res[-50:-10]), np.mean(res[-10:])]
plt.bar(img_type, prs)
plt.title('Average pearson correlation')


# custom loss
def ssim_loss(y_true, y_pred):
    return tf.math.subtract(tf.constant(1.0), tf.image.ssim(y_true, y_pred, 1.0, filter_size=2))

def psnr_loss(y_true, y_pred):
    return -tf.image.psnr(y_true, y_pred, max_val=1)

# custom metrics
def ssim(y_true, y_pred):
    return tf.reduce_mean(tf.image.ssim(y_true, y_pred, 1.0, filter_size=2))

def psnr(y_true, y_pred):
    return tf.image.psnr(y_true, y_pred, max_val=1)

optimizer = tf.keras.optimizers.Adam()
epochs = 100
N = len(x_train)
batch_size = 50

for epoch in range(epochs):
    start = time.time()

    # use GradientTape so that we can calculate the gradient of loss w.r.t. parameters
    with tf.GradientTape() as tape:
        if batch_size is not None and batch_size < N:
            perm = np.random.permutation(N)
            loss_value = 0
            for i in range(0, N, batch_size):
                # calculate the loss
                y_pred = gen(x_train[perm[i:i+batch_size]], training=True)
                loss_value += ssim_loss(y_pred, y_train[perm[i:i+batch_size]])
        else:
            # calculate the loss
            loss_value = ssim_loss(gen(x_train, training=True), y_train)

    # calculate gradients and convert to 1D tf.Tensor
    grads = tape.gradient(loss_value, gen.trainable_variables)

    print ('Time for epoch {} is {} sec, loss is {}.'.format(epoch + 1, time.time()-start, loss_value))
    
    

# L-BFGS
def function_factory(model, loss, train_x, train_y, batch_size=10):

    # obtain the shapes of all trainable parameters in the model
    shapes = tf.shape_n(model.trainable_variables)
    n_tensors = len(shapes)

    # we'll use tf.dynamic_stitch and tf.dynamic_partition later, so we need to
    # prepare required information first
    count = 0
    idx = [] # stitch indices
    part = [] # partition indices

    for i, shape in enumerate(shapes):
        n = np.product(shape)
        idx.append(tf.reshape(tf.range(count, count+n, dtype=tf.int32), shape))
        part.extend([i]*n)
        count += n

    part = tf.constant(part)

    @tf.function
    def assign_new_model_parameters(params_1d):

        params = tf.dynamic_partition(params_1d, part, n_tensors)
        for i, (shape, param) in enumerate(zip(shapes, params)):
            model.trainable_variables[i].assign(tf.reshape(param, shape))

    # now create a function that will be returned by this factory
    @tf.function
    def f(params_1d):

        N = len(x_train)
        # use GradientTape so that we can calculate the gradient of loss w.r.t. parameters
        with tf.GradientTape() as tape:
            # update the parameters in the model
            assign_new_model_parameters(params_1d)
            if batch_size < N:
                perm = np.random.permutation(N)
                loss_value = 0
                for i in range(0, N, batch_size):
                    # calculate the loss
                    loss_value += loss(model(train_x[perm[i:i+batch_size]], training=True), train_y[perm[i:i+batch_size]])
            else:
                # calculate the loss
                loss_value = loss(model(train_x, training=True), train_y)

        # calculate gradients and convert to 1D tf.Tensor
        grads = tape.gradient(loss_value, model.trainable_variables)
        grads = tf.dynamic_stitch(idx, grads)

        # print out iteration & loss
        f.iter.assign_add(1)
        tf.print("Iter:", f.iter, "loss:", loss_value)

        # store loss value so we can retrieve later
        tf.py_function(f.history.append, inp=[loss_value], Tout=[])

        return loss_value, grads

    # store these information as members so we can use them outside the scope
    f.iter = tf.Variable(0)
    f.idx = idx
    f.part = part
    f.shapes = shapes
    f.assign_new_model_parameters = assign_new_model_parameters
    f.history = []

    return f

func = function_factory(gen, tf.keras.losses.MeanSquaredError(), x_train, y_train, batch_size=50)

# convert initial model parameters to a 1D tf.Tensor
init_params = tf.dynamic_stitch(func.idx, gen.trainable_variables)

# train the model with L-BFGS solver
results = tfp.optimizer.lbfgs_minimize(value_and_gradients_function=func, initial_position=init_params, max_iterations=500)

# after training, the final optimized parameters are still in results.position
# so we have to manually put them back to the model
func.assign_new_model_parameters(results.position)


# DCGAN
def discriminator(img_shape, kernel_size=5, strides=2):
    d_n_layers = 5

    inputs = tf.keras.Input(shape=img_shape, name='Input', dtype='float32')
    current_layer = tf.expand_dims(inputs, -1, name='d_channelexpand')

    for i in range(0, d_n_layers):
        d_n_filters = 32 * 2**i
        current_layer = tf.keras.layers.Conv2D(filters=d_n_filters, kernel_size=kernel_size, strides=strides, padding='same',
                                               name='d_{}_conv2d'.format(i))(current_layer)
        current_layer = tf.keras.layers.LeakyReLU(alpha=0.2, name='d_{}_leakyrelu'.format(i))(current_layer)
        current_layer = tf.keras.layers.Dropout(0.25, name='d_{}_dropout'.format(i))(current_layer)
        current_layer = tf.keras.layers.BatchNormalization(momentum=0.8, name='d_{}_batchnorm'.format(i))(current_layer)

    current_layer = tf.keras.layers.Flatten(name='d_out_flatten')(current_layer)
    outputs = tf.keras.layers.Dense(units=1, activation='sigmoid', name='d_out')(current_layer)

    return tf.keras.Model(inputs, outputs, name='discriminator')

discriminator = discriminator(tuple(np.array(y_train.shape)[1:]))

discriminator.summary()

# This method returns a helper function to compute cross entropy loss
cross_entropy = tf.keras.losses.BinaryCrossentropy(from_logits=True)

def discriminator_loss(real_output, fake_output):
    real_loss = cross_entropy(tf.ones_like(real_output), real_output)
    fake_loss = cross_entropy(tf.zeros_like(fake_output), fake_output)
    total_loss = real_loss + fake_loss
    return total_loss

def generator_loss(fake_output):
    return cross_entropy(tf.ones_like(fake_output), fake_output)

generator_optimizer = tf.keras.optimizers.Adam()
discriminator_optimizer = tf.keras.optimizers.Adam()
epochs = 100
N = len(x_train)
batch_size = 50

for epoch in range(epochs):
    start = time.time()
    perm = np.random.permutation(N)
    gen_loss = 0
    disc_loss = 0

    for i in range(0, N, batch_size):
        with tf.GradientTape() as gen_tape, tf.GradientTape() as disc_tape:
            generated_images = gen(x_train[perm[i:i+batch_size]], training=True)

            real_output = discriminator(y_train[perm[i:i+batch_size]], training=True)
            fake_output = discriminator(generated_images, training=True)

            gen_loss = generator_loss(fake_output)
            disc_loss = discriminator_loss(real_output, fake_output)

    gradients_of_generator = gen_tape.gradient(gen_loss, gen.trainable_variables)
    gradients_of_discriminator = disc_tape.gradient(disc_loss, discriminator.trainable_variables)

    generator_optimizer.apply_gradients(zip(gradients_of_generator, unet.trainable_variables))
    discriminator_optimizer.apply_gradients(zip(gradients_of_discriminator, discriminator.trainable_variables))
    print ('Time for epoch {} is {} sec, gen_loss is {}, disc_loss is {}.'.format(epoch + 1, time.time()-start, gen_loss, disc_loss))
