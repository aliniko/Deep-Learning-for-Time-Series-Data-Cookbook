from pytorch_lightning.callbacks import EarlyStopping
import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler
from torch import nn
import torch
import torch.nn.functional as F
from pytorch_forecasting import TimeSeriesDataSet
from pytorch_lightning import LightningModule, Trainer, LightningDataModule

N_LAGS = 7
HORIZON = 1

mvtseries = pd.read_csv('assets/daily_multivariate_timeseries.csv',
                        parse_dates=['datetime'],
                        index_col='datetime')

n_vars = mvtseries.shape[1]


class MultivariateSeriesDataModule(LightningDataModule):
    def __init__(self,
                 data: pd.DataFrame,
                 n_lags: int,
                 horizon: int,
                 test_size: float,
                 batch_size: int):
        super().__init__()

        self.data = data
        self.ts_variable_names = self.data.columns.tolist()
        self.batch_size = batch_size
        self.test_size = test_size
        self.n_lags = n_lags
        self.horizon = horizon

        self.training = None
        self.validation = None
        self.test = None
        self.predict_set = None

    def setup(self, stage=None):
        self.data['time_index'] = np.arange(self.data.shape[0])
        self.data['group_id'] = 0

        unique_times = self.data['time_index'].sort_values().unique()

        train_index, test_index = train_test_split(unique_times,
                                                   test_size=self.test_size,
                                                   shuffle=False)

        train_index, validation_index = train_test_split(train_index,
                                                         test_size=0.1,
                                                         shuffle=False)

        training_df = self.data.loc[self.data['time_index'].isin(train_index), :]
        validation_df = self.data.loc[self.data['time_index'].isin(validation_index), :]
        test_df = self.data.loc[self.data['time_index'].isin(test_index), :]

        self.training = TimeSeriesDataSet(
            data=training_df,
            time_idx="time_index",
            target="Incoming Solar",
            group_ids=['group_id'],
            max_encoder_length=self.n_lags,
            max_prediction_length=self.horizon,
            time_varying_unknown_reals=self.ts_variable_names,
            scalers={k: MinMaxScaler() for k in self.ts_variable_names if k != 'Incoming Solar'}
        )

        self.validation = TimeSeriesDataSet.from_dataset(self.training, validation_df)
        self.test = TimeSeriesDataSet.from_dataset(self.training, test_df)
        self.predict_set = TimeSeriesDataSet.from_dataset(self.training, self.data, predict=True)

    def train_dataloader(self):
        return self.training.to_dataloader(batch_size=self.batch_size, shuffle=False)

    def val_dataloader(self):
        return self.validation.to_dataloader(batch_size=self.batch_size, shuffle=False)

    def test_dataloader(self):
        return self.test.to_dataloader(batch_size=self.batch_size, shuffle=False)

    def predict_dataloader(self):
        return self.predict_set.to_dataloader(batch_size=self.batch_size, shuffle=False)


class MultivariateLSTM(LightningModule):
    def __init__(self, input_dim, hidden_dim, num_layers, output_dim):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        h0 = torch.zeros(self.lstm.num_layers, x.size(0), self.hidden_dim).to(self.device)
        c0 = torch.zeros(self.lstm.num_layers, x.size(0), self.hidden_dim).to(self.device)
        out, _ = self.lstm(x, (h0, c0))
        out = self.fc(out[:, -1, :])

        return out

    def training_step(self, batch, batch_idx):
        x, y = batch
        y_pred = self(x['encoder_cont'])
        y_pred = y_pred.squeeze(1)
        loss = F.mse_loss(y_pred, y[0])
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        y_pred = self(x["encoder_cont"])

        y_pred = y_pred.squeeze(1)

        loss = F.mse_loss(y_pred, y[0])
        self.log('val_loss', loss)
        return loss

    def test_step(self, batch, batch_idx):
        x, y = batch
        y_pred = self(x['encoder_cont'])
        y_pred = y_pred.squeeze(1)
        loss = F.mse_loss(y_pred, y[0])
        self.log('test_loss', loss)

    def predict_step(self, batch, batch_idx, dataloader_idx=0):
        x, y = batch

        y_pred = self(x['encoder_cont'])
        y_pred = y_pred.squeeze(1)

        return y_pred

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=0.01)


datamodule = MultivariateSeriesDataModule(data=mvtseries,
                                          n_lags=N_LAGS,
                                          horizon=HORIZON,
                                          test_size=0.3,
                                          batch_size=16)

model = MultivariateLSTM(input_dim=n_vars, hidden_dim=10, num_layers=1, output_dim=1)

early_stop_callback = EarlyStopping(
    monitor='val_loss',
    min_delta=0.00,
    patience=4,
    verbose=True,
    mode='min'
)

trainer = Trainer(max_epochs=100, callbacks=[early_stop_callback])
trainer.fit(model, datamodule)