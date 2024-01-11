from pprint import pprint

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

import torch
import torch.nn as nn
import torch.nn.functional as F
from pytorch_forecasting import TimeSeriesDataSet
import lightning.pytorch as pl
from gluonts.dataset.repository.datasets import get_dataset, dataset_names
from sktime.transformations.series.fourier import FourierFeatures
from lightning.pytorch.callbacks import EarlyStopping

pprint(dataset_names)
dataset = get_dataset('nn5_daily_without_missing', regenerate=False)

print(len(list(dataset.train)))
print(len(list(dataset.train)[0]['target']))

N_LAGS = 7
HORIZON = 3


class GlobalDataModuleSeas(pl.LightningDataModule):
    def __init__(self,
                 data,
                 n_lags: int,
                 horizon: int,
                 test_size: float,
                 batch_size: int):
        super().__init__()

        self.data = data
        self.batch_size = batch_size
        self.test_size = test_size
        self.n_lags = n_lags
        self.horizon = horizon

        self.training = None
        self.validation = None
        self.test = None
        self.predict_set = None

    def setup(self, stage=None):
        # data_list = list(dataset.train)
        data_list = list(self.data.train)

        data_list = [pd.Series(ts['target'],
                               index=pd.date_range(start=ts['start'].to_timestamp(),
                                                   freq=ts['start'].freq,
                                                   periods=len(ts['target'])))
                     for ts in data_list]

        tseries_df = pd.concat(data_list, axis=1)
        tseries_df['time_index'] = np.arange(tseries_df.shape[0])

        ts_df = tseries_df.reset_index().melt(['time_index', 'index'])
        ts_df = ts_df.rename(columns={'variable': 'group_id'})

        fourier = FourierFeatures(sp_list=[7],
                                  fourier_terms_list=[2],
                                  keep_original_columns=False)

        fourier_features = fourier.fit_transform(ts_df['index'])

        ts_df = pd.concat([ts_df, fourier_features], axis=1).drop('index', axis=1)

        unique_times = ts_df['time_index'].sort_values().unique()

        tr_ind, ts_ind = \
            train_test_split(unique_times,
                             test_size=self.test_size,
                             shuffle=False)

        tr_ind, vl_ind = \
            train_test_split(tr_ind,
                             test_size=0.1,
                             shuffle=False)

        training_df = ts_df.loc[ts_df['time_index'].isin(tr_ind), :]
        validation_df = ts_df.loc[ts_df['time_index'].isin(vl_ind), :]
        test_df = ts_df.loc[ts_df['time_index'].isin(ts_ind), :]

        self.training = TimeSeriesDataSet(
            data=training_df,
            time_idx='time_index',
            target='value',
            group_ids=['group_id'],
            max_encoder_length=self.n_lags,
            max_prediction_length=self.horizon,
            time_varying_unknown_reals=['value'],
            time_varying_known_reals=['sin_7_1',
                                      'cos_7_1',
                                      'sin_7_2',
                                      'cos_7_2']
        )

        self.validation = TimeSeriesDataSet.from_dataset(self.training, validation_df)
        self.test = TimeSeriesDataSet.from_dataset(self.training, test_df)
        self.predict_set = TimeSeriesDataSet.from_dataset(self.training, ts_df, predict=True)

    def train_dataloader(self):
        return self.training.to_dataloader(batch_size=self.batch_size, shuffle=False)

    def val_dataloader(self):
        return self.validation.to_dataloader(batch_size=self.batch_size, shuffle=False)

    def test_dataloader(self):
        return self.test.to_dataloader(batch_size=self.batch_size, shuffle=False)

    def predict_dataloader(self):
        return self.predict_set.to_dataloader(batch_size=1, shuffle=False)


class GlobalLSTM(pl.LightningModule):
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

        loss = F.mse_loss(y_pred, y[0])
        self.log('train_loss', loss)
        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch

        y_pred = self(x['encoder_cont'])

        loss = F.mse_loss(y_pred, y[0])
        self.log('val_loss', loss)
        return loss

    def test_step(self, batch, batch_idx):
        x, y = batch

        y_pred = self(x['encoder_cont'])

        loss = F.mse_loss(y_pred, y[0])
        self.log('test_loss', loss)

    def predict_step(self, batch, batch_idx, dataloader_idx=0):
        x, y = batch

        y_pred = self(x['encoder_cont'])

        return y_pred

    def configure_optimizers(self):
        return torch.optim.Adam(self.parameters(), lr=0.01)


model = GlobalLSTM(input_dim=5,
                   hidden_dim=32,
                   num_layers=1,
                   output_dim=HORIZON)

datamodule = GlobalDataModuleSeas(data=dataset,
                                  n_lags=N_LAGS,
                                  horizon=HORIZON,
                                  batch_size=128,
                                  test_size=0.3)

early_stop_callback = EarlyStopping(monitor="val_loss",
                                    min_delta=1e-4,
                                    patience=10,
                                    verbose=False,
                                    mode="min")

trainer = pl.Trainer(max_epochs=20, callbacks=[early_stop_callback])

trainer.fit(model, datamodule)

trainer.test(model=model, datamodule=datamodule)
forecasts = trainer.predict(model=model, datamodule=datamodule)