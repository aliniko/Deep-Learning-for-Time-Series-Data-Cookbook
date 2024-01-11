from gluonts.dataset.common import ListDataset
from gluonts.dataset.common import FieldName
from gluonts.torch.model.tft import TemporalFusionTransformerEstimator
from gluonts.evaluation import make_evaluation_predictions
from gluonts.dataset.repository.datasets import get_dataset
import matplotlib.pyplot as plt

N_LAGS = 7
HORIZON = 7

# Setting up the dataset
dataset = get_dataset("nn5_daily_without_missing", regenerate=False)

train_ds = ListDataset(
    [
        {FieldName.TARGET: entry["target"], FieldName.START: entry["start"]}
        for entry in dataset.train
    ],
    freq=dataset.metadata.freq,
)

# Defining the Temporal Fusion Transformer estimator
estimator = TemporalFusionTransformerEstimator(
    prediction_length=HORIZON,
    context_length=N_LAGS,
    freq=dataset.metadata.freq,
    trainer_kwargs={"max_epochs": 100},
)

# Training the TFT model
predictor = estimator.train(train_ds)

# Making predictions
forecast_it, ts_it = make_evaluation_predictions(
    dataset=dataset.test,
    predictor=predictor,
    num_samples=100,
)
forecasts = list(forecast_it)
tss = list(ts_it)

# Plotting
fig, ax = plt.subplots(1, 1, figsize=(10, 6))

# Plot for TFT
ts_entry = tss[0]
ax.plot(ts_entry[-150:].to_timestamp())  # only the last 150 data points for clarity
forecasts[0].plot(show_label=True, ax=ax, intervals=())
ax.set_title("Forecast with Temporal Fusion Transformer")
ax.legend()

plt.tight_layout()
plt.show()