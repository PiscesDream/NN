# location-wise hard attention
from NN.common.layers import InputLayer, FullConnectLayer, SoftmaxLayer
from NN.common.toolkits import generate_wb 
from NN.common.nets import NetModel
import sys

import numpy as np
import theano
import theano.tensor as T

from theano.sandbox.rng_mrg import MRG_RandomStreams

#from NN.RNN.naive import RNN

class AttentionUnit(object):
    # LSTM + location
    def __init__(self, x, glimpse_shape, glimpse_times, dim_hidden, rng, rng_std=1.0, activation=T.tanh, bptt_truncate=-1, name='AttentionModel', minimum_p=1e-10):
        # random for rng
        self.rng = rng
        self.rng_std = rng_std
        # n * W * H --> n * dim_input --> n * dim_hidden
        self.glimpse_shape = glimpse_shape
        dim_input = np.prod(glimpse_shape)

#       W_x0 = generate_wb(dim_input, 4*dim_hidden, '{}_x'.format(name), params=['w'])
#       W_h0, b_h0 = generate_wb(dim_hidden, 4*dim_hidden, '{}_hidden'.format(name))
        W_x = generate_wb(dim_input, 4*dim_hidden, '{}_x'.format(name), params=['w'])
        W_c = generate_wb(dim_hidden, 3*dim_hidden, '{}_c'.format(name), params=['w'])
        W_h, b_h = generate_wb(dim_hidden, 4*dim_hidden, '{}_hidden'.format(name))

        w_location, b_location = generate_wb(dim_hidden, 2, '{}->location'.format(name), params=['w', 'b'])
#       b_location.set_value([14, 14])

        def forward(times, s_prev, C_prev, x, W_x, W_c, W_h, b_h, w_l, b_l): #, w_l0, b_l0): 
            # current input, previous hidden state, w_input, w_hidden, w_output
            # x.shape = n * W * H 
            # s_prev, C_prev.shape = N * dim_hidden

            # get location vector
#           loc_mean = activation( s_prev.dot(w_l) + b_l )  # n * 2
#           loc_mean = activation(s_prev.dot(w_l0)+b_l0).dot(w_l) + b_l  # n * 2  TODO
            loc_mean = s_prev.dot(w_l) + b_l  # n * 2  TODO
            # glimpse
            glimpse, loc = self._glimpse(x, loc_mean) # n * dim_hidden, n * 2
            # input

            # LSTM
            res    =   glimpse.dot(W_x) +  s_prev.dot(W_h)      + b_h.dimshuffle('x', 0) 
            peephole = C_prev.dot(W_c)
            f = T.nnet.sigmoid(res[:, 0*dim_hidden:1*dim_hidden] + peephole[:, 0*dim_hidden:1*dim_hidden]) # N * dh
            i = T.nnet.sigmoid(res[:, 1*dim_hidden:2*dim_hidden] + peephole[:, 1*dim_hidden:2*dim_hidden]) # N * dh
            C_hat =     T.tanh(res[:, 2*dim_hidden:3*dim_hidden]) # N * dh
            o = T.nnet.sigmoid(res[:, 3*dim_hidden:4*dim_hidden] + peephole[:, 2*dim_hidden:3*dim_hidden]) # N * dh
            C = f*C_prev + i*C_hat # N * dh
            s = o * T.tanh(C)      # N * dh
            return s, C, loc, loc_mean, glimpse, \
                    T.concatenate([\
                    res[:, 0*dim_hidden:1*dim_hidden], 
                    res[:, 1*dim_hidden:2*dim_hidden], 
                    res[:, 2*dim_hidden:3*dim_hidden], 
                    res[:, 3*dim_hidden:4*dim_hidden],
                    f, i, C_hat, o, C, s]) # n*dim_h, n*dim_h, n * 2, n * 2


        [s, C, loc, loc_mean, glimpse, innerstate], updates = theano.scan(
            fn=forward,
            sequences = T.arange(glimpse_times), #x.swapaxes(0, 1),
            outputs_info = [T.zeros((x.shape[0], dim_hidden)), 
                            T.zeros((x.shape[0], dim_hidden)), 
                            None, None, None, None], 
            non_sequences = [x, W_x, W_c, W_h, b_h, w_location, b_location],#w_location0, b_location0],
            truncate_gradient=bptt_truncate,
            strict = True)
        # s: Time * n * dim_hidden
        # loc: Time * n * 2

        self.output = s.swapaxes(0, 1) # N * Time * dim_hidden
        self.cell = s.swapaxes(0, 1)
        self.location = loc.swapaxes(0, 1) # N * T * dim_h
        self.params = [W_x, W_c, W_h, b_h]
        self.reinforceParams = [w_location, b_location] #, w_location0, b_location0]

        # for debug
        self.location_mean = loc_mean.swapaxes(0, 1) # N * T * 2
        self.glimpse = glimpse.swapaxes(0, 1) # N * Time * glimpse_shape
        self.location_p = T.maximum( 1.0/(T.sqrt(2*np.pi)*rng_std)*T.exp(-((loc-loc_mean)**2)/(2.0*rng_std**2)), minimum_p ).swapaxes(0,1) # N * T * 2  locx and locy is independent
#       self.location_logp = - float(1.0/(2.0*rng_std**2)) * ((loc-loc_mean)**2).swapaxes(0,1)
                # this part is useless in training >> - T.log(T.sqrt(2*T.pi)*rng_std) 
        self.innerstate = innerstate.swapaxes(0, 1)


    def _glimpse(self, x, loc_mean):
        '''
            x: tensor3 (N, W, H)
            raw_loc: matrix (N, 2) mean 
            loc: matrix (N, 2) center point
        '''
#       loc = T.cast(T.maximum(loc_mean*x.shape[1:], 0), 'int32')
#       loc = T.cast(self.rng.normal(size=loc_mean.shape, avg=loc_mean, std=self.rng_std), 'int32')

        loc = self.rng.normal(size=(x.shape[0], 2), avg=loc_mean, std=self.rng_std) \
            - T.stack(self.glimpse_shape[0]/2., self.glimpse_shape[1]/2.).dimshuffle('x', 0)  # random top-left corner
        loc = T.cast(T.round(loc), 'int32') 
        
        locx = T.clip(loc[:,0], 0, x.shape[1]-self.glimpse_shape[0])  #*self.glimpse_shape[2]
        locy = T.clip(loc[:,1], 0, x.shape[2]-self.glimpse_shape[1])  #*self.glimpse_shape[2] 
        def glimpse_each(xi, locxi, locyi):
            return xi[locxi:locxi+self.glimpse_shape[0], locyi:locyi+self.glimpse_shape[1]].flatten()
#           g = []
#           upper = locxi
#           bottom = locxi+self.glimpse_shape[2]*self.glimpse_shape[0]
#           left = locyi
#           right = locyi+self.glimpse_shape[2]*self.glimpse_shape[1]
#           for level in range(1, self.glimpse_shape[2]+1)[::-1]:
#               gi = xi[upper:bottom, left:right]
#               g.append( T.signal.downsample.max_pool_2d(gi, (level, level)) )

#               upper += self.glimpse_shape[0]/2
#               bottom -= self.glimpse_shape[0]/2
#               left += self.glimpse_shape[1]/2
#               right -= self.glimpse_shape[1]/2
#           return T.stack(g).flatten()

        x, updates = theano.scan(
            fn = glimpse_each,
            sequences = [x, locx, locy],
            strict=True)
        loc = T.stack(locx+self.glimpse_shape[0]/2., locy+self.glimpse_shape[1]/2.).T # record center
        # x: N * dim_in
        # loc: N * 2
        return x, loc # crop


class AttentionModel(NetModel):
    def __init__(self, 
        glimpse_shape, glimpse_times, 
        dim_hidden, dim_fc, dim_out, 
        reward_base, 
        rng_std=1.0, activation=T.tanh, bptt_truncate=-1, 
        lmbd=0.1, # gdupdate + lmbd*rlupdate
        DEBUG=False,
        ): 
#       super(AttentionUnit, self).__init__()

        if reward_base == None: 
            reward_base = np.zeros((glimpse_times)).astype('float32')
            reward_base[-1] = 1.0
        x = T.ftensor3('x')  # N * W * H 
        y = T.ivector('y')  # label 
        lr = T.fscalar('lr')
        reward_base = theano.shared(name='reward_base', value=np.array(reward_base).astype(theano.config.floatX), borrow=True) # Time (vector)
        reward_bias = T.fvector('reward_bias')
#       rng = T.shared_randomstreams.RandomStreams(123)
        rng = MRG_RandomStreams(np.random.randint(9999999))
    
        i = InputLayer(x)
        au = AttentionUnit(x, glimpse_shape, glimpse_times, dim_hidden, rng, rng_std, activation, bptt_truncate)
#       All hidden states are put into decoder
#       layers = [i, au, InputLayer(au.output[:,:,:].flatten(2))]
#       dim_fc = [glimpse_times*dim_hidden] + dim_fc + [dim_out]
#       Only the last hidden states
        layers = [i, au, InputLayer(au.output[:,-1,:])]
        dim_fc = [dim_hidden] + dim_fc + [dim_out]
        for Idim, Odim in zip(dim_fc[:-1], dim_fc[1:]):
            fc = FullConnectLayer(layers[-1].output, Idim, Odim, activation, 'FC')
            layers.append(fc)
        sm = SoftmaxLayer(layers[-1].output)
        layers.append(sm)

        output = sm.output       # N * classes 
        hidoutput = au.output    # N * dim_output 
        location = au.location   # N * T * dim_hidden
        prediction = output.argmax(1) # N

        # calc
        equalvec = T.eq(prediction, y) # [0, 1, 0, 0, 1 ...]
        correct = T.cast(T.sum(equalvec), 'float32')
#       noequalvec = T.neq(prediction, y)
#       nocorrect = T.cast(T.sum(noequalvec), 'float32')
        logLoss = T.log(output)[T.arange(y.shape[0]), y] # 
#       reward_biased = T.outer(equalvec, reward_base - reward_bias.dimshuffle('x', 0))
        reward_biased = T.outer(equalvec, reward_base) - reward_bias.dimshuffle('x', 0)
            # N * Time
            # (R_t - b_t), where b = E[R]
        
        # gradient descent
        gdobjective = logLoss.sum()/x.shape[0]  # correct * dim_output (only has value on the correctly predicted sample)
        gdparams = reduce(lambda x, y: x+y.params, layers, []) 
        gdupdates = map(lambda x: (x, x+lr*T.grad(gdobjective, x)), gdparams)

        # reinforce learning
        # without maximum, then -log(p) will decrease the p
        rlobjective = (T.maximum(reward_biased.dimshuffle(0, 1, 'x'), 0) * T.log(au.location_p)).sum() / correct 
            # location_p: N * Time * 2
            # location_logp: N * Time
            # reward_biased: N * 2
        rlparams = au.reinforceParams 
        rlupdates = map(lambda x: (x, x+lr*lmbd*T.grad(rlobjective, x)), rlparams)


        # Hidden state keeps unchange in time
        deltas = T.stack(*[((au.output[:,i,:].mean(0)-au.output[:,i+1,:].mean(0))**2).sum()  for i in xrange(glimpse_times-1)])
            # N * Time * dim_hidden
         
        print 'compile step()'
        self.step = theano.function([x, y, lr, reward_bias], [gdobjective, rlobjective, correct, T.outer(equalvec, reward_base)], updates=gdupdates+rlupdates)
    #       print 'compile gdstep()'
    #       self.gdstep = theano.function([x, y, lr], [gdobjective, correct, location], updates=gdupdates)
    #       print 'compile rlstep()'
    #       self.rlstep = theano.function([x, y, lr], [rlobjective], updates=rlupdates)
        print 'compile predict()'
        self.predict = theano.function([x], prediction)
        if DEBUG:
            print 'compile glimpse()'
            self.glimpse = theano.function([x], au.glimpse) #[layers[-3].output, fc.output])
            print 'compile innerstate()'
            self.getinnerstate = theano.function([x], au.innerstate)
            print 'compile forward()'
            self.forward = theano.function([x], map(lambda x: x.output, layers)) #[layers[-3].output, fc.output])
            print 'compile error()'
            self.error = theano.function([x, y, reward_bias], [gdobjective, rlobjective])
        print 'compile locate()'
        self.locate = theano.function([x], [au.location_mean, location]) #[layers[-3].output, fc.output])
        print 'compile debug()'
        self.debug = theano.function([x, y, lr, reward_bias], [deltas, au.location_p], on_unused_input='warn')


        # self.xxx
        self.layers = layers
        self.params = gdparams + rlparams
        self.glimpse_times = glimpse_times

    def fit(self, x, y, 
            batch_size,
            lr=1e-2,
            max_iter=100000, 
            test_iter=1000,      # test on validation set
            disp_iter=10,        # display
            lr_iter=100,         # update lr
            reward_iter=100,
            reward_base=None,
            decay=0.9,
            gamma=0.80, # the reward bias update speed
            quick_save=(0, 'temp.model'),
            val=None):
        valx, valy = val if val !=None else (x, y)

        batch_count = len(x)/batch_size
        
        np.set_printoptions(precision=3, linewidth=np.inf)
        def npinline(x):
            return map(lambda loc: tuple(map(lambda loci: round(loci, 3), loc)), x.tolist())

        lastcost = np.inf
        gdcost = []
        rlcost = []
        correct = []
        reward = np.zeros((self.glimpse_times)).astype('float32')
        for i in xrange(max_iter):
            if i%test_iter == 0:
                cor = (self.predict(valx) == valy).sum()
                print '\n\tacc = {}/{} = {}'.format(cor, len(valy), float(cor)/len(valy))
               #print zip(valy, self.predict(valx))
            if i % lr_iter == 0:
                lr *= decay
                pass
#           if i % reward_iter == 0:
#               reward = []
            if quick_save[0] != 0 and i % quick_save[0] == 0:
                print 'saving ...'
                self.save_params(quick_save[1], verbose=1)

            # update
            batch_index = np.random.randint(batch_count)
            start = batch_index*batch_size
            end = (batch_index+1)*batch_size

            inputs = (x[start:end], y[start:end], lr, reward)
            gdcosti, rlcosti, correcti, rewardi = self.step(*inputs)
            gdcost.append(gdcosti)
            rlcost.append(rlcosti)
            correct.append(correcti)
            reward = gamma*reward + (1-gamma)*rewardi.mean(0) # N * Time -> Time

            if i % disp_iter == 0: 
                print 'Iter[{}] lr={}'.format(i, lr)
                print '\tGDcost: {}\tRLcost: {}\tcorrect: {}/{}\treward_bias: {}'.\
                    format(np.mean(gdcost), np.mean(rlcost), np.mean(correct), batch_size, reward) 
#               print '\ty: {}'.format(y[0])
                loc_mean, location = self.locate(x[start:end])
                print '\tloc_mean: {}'.format( npinline(loc_mean.mean(0)) )
                print '\tlocation: {}'.format( npinline(location.mean(0)) )
                da, db = self.debug(*inputs)
                print '\tloc_p: {}'.format( npinline(db.mean(0)) )
                print '\tdetlas: {}'.format(da.tolist())
#               print '\tloc_mean: {}'.format( npinline(self.debug(x[start:end], y[start:end], lr)[0].reshape(-1, 2)) )
#               print '\tlocation: {}'.format( npinline(self.locate(x[start:end])[0].reshape(-1, 2)) )
                sys.stdout.flush()
                cost = []
                correct = [] 
 

