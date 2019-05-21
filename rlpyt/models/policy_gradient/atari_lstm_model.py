
import torch
import torch.nn.functional as F

from rlpyt.utils.collections import namedarraytuple
from rlpyt.utils.tensor import infer_leading_dims, restore_leading_dims
from rlpyt.models.utils import conv2d_output_shape


RnnState = namedarraytuple("RnnState", ["h", "c"])  # For downstream namedarraytuples to work


class AtariLstmModel(torch.nn.Module):

    def __init__(
            self,
            image_shape,
            output_dim,
            # conv_channels,
            # conv_sizes,
            # conv_strides,
            # conv_pads,
            # pool_sizes,
            hidden_size=256,
            lstm_size=256,
            lstm_layers=1,
            # name="atari_cnn_lstm",
            ):
        super().__init__()

        # Hard-code just to get it running.
        c, h, w = image_shape  # Track image shape along with conv definition.
        self.conv1 = torch.nn.Conv2d(
            in_channels=c,
            out_channels=16,
            kernel_size=8,
            stride=1,
            padding=0,
        )
        h, w = conv2d_output_shape(h, w, kernel_size=8, stride=1, padding=0)

        self.maxp1 = torch.nn.MaxPool2d(2)
        h, w = conv2d_output_shape(h, w, kernel_size=2, stride=2, padding=0)

        self.conv2 = torch.nn.Conv2d(
            in_channels=16,
            out_channels=32,
            kernel_size=4,
            stride=1,
            padding=0,
        )
        h, w = conv2d_output_shape(h, w, kernel_size=4, stride=1, padding=0)

        self.maxp2 = torch.nn.MaxPool2d(2)
        h, w = conv2d_output_shape(h, w, kernel_size=2, stride=2, padding=0)

        fc_in_size = h * w * 32
        self.fc = torch.nn.Linear(fc_in_size, hidden_size)
        lstm_in_size = hidden_size + output_dim + 1
        self.lstm = torch.nn.LSTM(lstm_in_size, lstm_size, lstm_layers)
        self.linear_pi = torch.nn.Linear(lstm_size, output_dim)
        self.linear_v = torch.nn.Linear(lstm_size, 1)

        # in_channels = image_shape[0]
        # self.conv_layers = list()
        # self.conv_pool_layers = list()
        # for i in range(len(conv_channels)):
        #     conv_layer = torch.nn.Conv2d(
        #         in_channels=in_channels,
        #         out_channels=conv_channels[i],
        #         kernel_size=conv_sizes[i],
        #         stride=conv_strides[i],
        #         padding=conv_pads[i],
        #     )
        #     self.conv_layers.append(conv_layer)
        #     if pool_sizes[i] > 1:
        #         pool_layer = torch.nn.MaxPool2d(pool_sizes[i])
        #         self.conv_pool_layers.append(pool_layer)
        #     in_channels = conv_channels[i]

        # test_mat = torch.zeros(1, **image_shape)
        # for conv_pool_layer in self.conv_pool_layers:
        #     test_mat = conv_pool_layer(test_mat)
        # self.conv_out_size = int(np.prod(test_mat.shape))

        # if hidden_size > 0:
        #     self.hidden_layer = torch.nn.Linear(self.conv_out_size, hidden_size)
        #     lstm_input_size = hidden_size
        # else:
        #     self.hidden_layer = None
        #     lstm_input_size = self.conv_out_size
        # lstm_input_size += sum([s.size for s in env_spec.action_spaces]) + 1

        # self.lstm_layer = torch.nn.LSTM(
        #     input_size=lstm_input_size,
        #     hidden_size=lstm_size,
        #     num_layers=lstm_layers,
        # )

    def forward(self, image, prev_action, prev_reward, init_rnn_state):
        """Feedforward layers process as [T*B,H], recurrent ones as [T,B,H].
        Return same leading dims as input, can be [T,B], [B], or [].
        (Same forward used for sampling and training.)"""
        img = image.to(torch.float)  # Expect torch.uint8 inputs
        img = img.mul_(1. / 255)  # From [0-255] to [0-1], in place.

        # Infer (presence of) leading dimensions: [T,B], [B], or [].
        img_shape, T, B, has_T, has_B = infer_leading_dims(img, 3)

        img = img.view(T * B, *img_shape)  # Fold time and batch dimensions.
        img = F.relu(self.maxp1(self.conv1(img)))
        img = F.relu(self.maxp2(self.conv2(img)))
        fc_out = F.relu(self.fc(img.view(T * B, -1)))

        lstm_input = torch.cat([
            fc_out.view(T, B, -1),
            prev_action.view(T, B, -1),  # Assumed onehot.
            prev_reward.view(T, B, 1),
            ], dim=2)
        if init_rnn_state is not None:  # [B,N,H] --> [N,B,H]
            init_rnn_state = tuple(torch.transpose(hc, 0, 1).contiguous()
                for hc in init_rnn_state)
        lstm_out, (hn, cn) = self.lstm(lstm_input, init_rnn_state)
        next_rnn_state = (hn.transpose(0, 1), cn.transpose(0, 1))  # --> [B,N,H]
        lstm_flat = lstm_out.view(T * B, -1)

        # pi = F.softmax(self.linear_pi(lstm_flat), dim=-1)
        # v = self.linear_v(lstm_flat).squeeze(-1)
        # DEBUG: bypass the LSTM.
        pi = F.softmax(self.linear_pi(fc_out), dim=-1)
        v = self.linear_v(fc_out).squeeze(-1)

        # Restore leading dimensions: [T,B], [B], or [], as input.
        pi, v = restore_leading_dims((pi, v), T, B, has_T, has_B)
        next_rnn_state = restore_leading_dims(next_rnn_state, B=B, has_B=has_B)
        next_rnn_state = RnnState(*next_rnn_state)

        return pi, v, next_rnn_state