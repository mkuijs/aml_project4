import pandas as pd
import os.path as ospath
import argparse
import numpy as np
from os import makedirs
from itertools import tee
from csv import reader
import biosppy.signals.eeg as eeg
from sklearn.model_selection import train_test_split
from sklearn.feature_selection import SelectKBest
from sklearn.neighbors import KNeighborsClassifier as KNC
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from scipy.signal import savgol_filter, butter, lfilter
from tqdm import tqdm
from statistics import median as pymedian
from scipy.stats import entropy as sci_entropy
import pywt
from collections import Counter

import logging

logging.getLogger().setLevel(logging.INFO)
logging.basicConfig(format='%(asctime)s - %(message)s')

# Debug parameters
first_n_lines_input = 50


def perform_data_scaling(x_train, x_test):
    scaler = StandardScaler()
    x_train_whitened = scaler.fit_transform(x_train)
    x_test_whitened = scaler.transform(x_test)
    return x_train_whitened, x_test_whitened


def read_in_irregular_csv(path_to_file, skip_n_lines=1, debug=False):
    file_array = []
    with open(path_to_file, 'r') as csv_file:
        brain_waves_reader = reader(csv_file, delimiter=',', quotechar='|')
        for row_to_skip in range(skip_n_lines):
            next(brain_waves_reader)
        for i, row in enumerate(tqdm(brain_waves_reader)):
            if debug and i == first_n_lines_input:
                break
            file_array.append(np.array(row[1:], dtype=np.float32))
    return file_array


def butter_lowpass(cutoff, fs, order=5):
    nyq = 0.5 * fs  # Nyquist frequeny is half the sampling frequency
    normal_cutoff = cutoff / nyq
    b, a = butter(N=order, Wn=normal_cutoff, btype='lowpass', analog=False, output='ba')
    return b, a


def butter_lowpass_filter(data, cutoff, fs, order):
    b, a = butter_lowpass(cutoff, fs, order=order)
    y = lfilter(b, a, data)
    return y


def butter_highpass(cutoff, fs, order=5):
    nyq = 0.5 * fs  # Nyquist frequeny is half the sampling frequency
    normal_cutoff = cutoff / nyq
    b, a = butter(N=order, Wn=normal_cutoff, btype='highpass', analog=False, output='ba')
    return b, a


def butter_highpass_filter(data, cutoff, fs, order):
    b, a = butter_highpass(cutoff, fs, order=order)
    y = lfilter(b, a, data)
    return y


def butter_bandstop(lowcut, highcut, fs, order=5):
    nyq = 0.5 * fs  # Nyquist frequeny is half the sampling frequency
    normal_lowcut = lowcut / nyq
    normal_highcut = highcut / nyq
    b, a = butter(N=order, Wn=[normal_lowcut, normal_highcut], btype='bandstop', analog=False, output='ba')
    return b, a


def butter_bandstop_filter(data, lowcut, highcut, fs, order=5):
    b, a = butter_bandstop(lowcut, highcut, fs, order=order)
    y = lfilter(b, a, data)
    return y


def extract_manual_features(eeg1, eeg2, emg):
    manual_features_array = []
    for i in enumerate(tqdm(eeg1)):
        eeg.eeg(raw_ecg, sampling_rate=180, show=False)
        mean_template = np.mean(templates, axis=0)

        feature_extracted_samples = []
        rr_interval_statistics = extract_rr_interval(rpeaks)
        feature_extracted_samples.append(rr_interval_statistics[0])  # average RR interval
        feature_extracted_samples.append(rr_interval_statistics[1])  # median RR interval
        feature_extracted_samples.append(rr_interval_statistics[2])  # IQR RR interval
        feature_extracted_samples.append(average_r_amplitude(filtered, rpeaks) - median_r_amplitude(filtered, rpeaks))
        feature_extracted_samples.append(std_r_amplitude(filtered, rpeaks))  # standard deviation R amplitude
        feature_extracted_samples.append(iqr_r_amplitude(filtered, rpeaks))  # IQR R amplitude
        feature_extracted_samples.append(ecg_domain(mean_template))
        # average peak amplitudes and indices
        p_peak = extract_p_peak(mean_template)
        t_peak = extract_t_peak(mean_template)
        r_peak = extract_r_peak(mean_template)
        feature_extracted_samples.append(p_peak[0])  # average amplitude of P peak
        feature_extracted_samples.append(t_peak[0])  # average amplitude of T peak
        feature_extracted_samples.append(r_peak[1] - p_peak[1])  # average PR interval
        feature_extracted_samples.append(t_peak[1] - r_peak[1])  # average RT interval
        # slope of P peak: a1
        feature_extracted_samples.append((p_peak[0] - mean_template[p_peak[1] - 2]) / (p_peak[1] - (p_peak[1] - 2)))
        # slope of P peak: a2
        feature_extracted_samples.append((p_peak[0] - mean_template[p_peak[1] + 2]) / (p_peak[1] - (p_peak[1] + 2)))
        # slope of R peak: a3
        feature_extracted_samples.append((r_peak[0] - mean_template[r_peak[1] - 2]) / (r_peak[1] - (r_peak[1] - 2)))
        # slope of R peak: a4
        feature_extracted_samples.append((r_peak[0] - mean_template[r_peak[1] + 2]) / (r_peak[1] - (r_peak[1] + 2)))
        # slope of T peak: a5
        feature_extracted_samples.append((t_peak[0] - mean_template[t_peak[1] - 2]) / (t_peak[1] - (t_peak[1] - 2)))
        # slope of T peak: a6
        feature_extracted_samples.append((t_peak[0] - mean_template[t_peak[1] + 2]) / (t_peak[1] - (t_peak[1] + 2)))

        manual_features_array.append(feature_extracted_samples)
    return np.array(manual_features_array)


def main(debug=False, outfile="out.csv"):
    output_pathname = "output"
    output_filepath = ospath.join(output_pathname, outfile)
    training_data_dir = ospath.join("data", "training")
    testing_data_dir = ospath.join("data", "testing")

    # Load training data
    logging.info("Reading in training data...")
    train_data_eeg1 = read_in_irregular_csv(ospath.join(training_data_dir, "train_eeg1.csv"), debug=debug)
    train_data_eeg2 = read_in_irregular_csv(ospath.join(training_data_dir, "train_eeg2.csv"), debug=debug)
    train_data_emg = read_in_irregular_csv(ospath.join(training_data_dir, "train_emg.csv"), debug=debug)
    train_data_y = pd.read_csv(ospath.join(training_data_dir, "train_labels.csv"), delimiter=",")["y"]
    if debug:
        train_data_y = train_data_y.head(first_n_lines_input)
    y_train_orig = train_data_y.values
    logging.info("Finished reading in data.")

    # Pre-processing step: mean subtraction
    eeg1_mean = np.mean(train_data_eeg1)
    train_data_eeg1 -= eeg1_mean

    eeg2_mean = np.mean(train_data_eeg2)
    train_data_eeg2 -= eeg2_mean

    emg_mean = np.mean(train_data_emg)
    train_data_emg -= emg_mean

    # Pre-processing step: Butterworth filtering
    train_data_eeg1 = list(map(lambda x: butter_lowpass_filter(x, 50, 128, 3), train_data_eeg1))
    train_data_eeg1 = list(map(lambda x: butter_highpass_filter(x, .5, 128, 3), train_data_eeg1))
    train_data_eeg1 = list(map(lambda x: butter_bandstop_filter(x, 47, 53, 128, 3), train_data_eeg1))
    smoothed_eeg1 = list(map(lambda x: butter_bandstop_filter(x, 97, 103, 128, 3), train_data_eeg1))

    train_data_eeg2 = list(map(lambda x: butter_lowpass_filter(x, 50, 128, 3), train_data_eeg2))
    train_data_eeg2 = list(map(lambda x: butter_highpass_filter(x, .5, 128, 3), train_data_eeg2))
    train_data_eeg2 = list(map(lambda x: butter_bandstop_filter(x, 47, 53, 128, 3), train_data_eeg2))
    smoothed_eeg2 = list(map(lambda x: butter_bandstop_filter(x, 97, 103, 128, 3), train_data_eeg2))

    train_data_emg = list(map(lambda x: butter_lowpass_filter(x, 50, 128, 3), train_data_emg))
    train_data_emg = list(map(lambda x: butter_highpass_filter(x, .5, 128, 3), train_data_emg))
    train_data_emg = list(map(lambda x: butter_bandstop_filter(x, 47, 53, 128, 3), train_data_emg))
    smoothed_emg = list(map(lambda x: butter_bandstop_filter(x, 97, 103, 128, 3), train_data_emg))

    # Extract features of training set
    logging.info("Extracting features...")
    logging.info("Finished extracting features")

    # Load raw ECG testing data
    logging.info("Reading in testing data...")
    test_data_eeg1 = read_in_irregular_csv(ospath.join(testing_data_dir, "test_eeg1.csv"), debug=debug)
    test_data_eeg2 = read_in_irregular_csv(ospath.join(testing_data_dir, "test_eeg2.csv"), debug=debug)
    test_data_emg = read_in_irregular_csv(ospath.join(testing_data_dir, "test_emg.csv"), debug=debug)
    logging.info("Finished reading in data.")

    # Pre-processing step: Butterworth filtering
    test_data_eeg1 = list(map(lambda x: butter_lowpass_filter(x, 50, 128, 3), test_data_eeg1))
    test_data_eeg1 = list(map(lambda x: butter_highpass_filter(x, .5, 128, 3), test_data_eeg1))
    test_data_eeg1 = list(map(lambda x: butter_bandstop_filter(x, 47, 53, 128, 3), test_data_eeg1))
    smoothed_eeg1_test = list(map(lambda x: butter_bandstop_filter(x, 97, 103, 128, 3), test_data_eeg1))

    test_data_eeg2 = list(map(lambda x: butter_lowpass_filter(x, 50, 128, 3), test_data_eeg2))
    test_data_eeg2 = list(map(lambda x: butter_highpass_filter(x, .5, 128, 3), test_data_eeg2))
    test_data_eeg2 = list(map(lambda x: butter_bandstop_filter(x, 47, 53, 128, 3), test_data_eeg2))
    smoothed_eeg2_test = list(map(lambda x: butter_bandstop_filter(x, 97, 103, 128, 3), test_data_eeg2))

    test_data_emg = list(map(lambda x: butter_lowpass_filter(x, 50, 128, 3), test_data_emg))
    test_data_emg = list(map(lambda x: butter_highpass_filter(x, .5, 128, 3), test_data_emg))
    test_data_emg = list(map(lambda x: butter_bandstop_filter(x, 47, 53, 128, 3), test_data_emg))
    smoothed_emg_test = list(map(lambda x: butter_bandstop_filter(x, 97, 103, 128, 3), test_data_emg))

    # Extract features of testing set
    logging.info("Extracting features...")
    logging.info("Finished extracting features")

    # Pre-processing step for meta-feature calculation: StandardScaler
    # x_train_fsel, x_test_fsel = perform_data_scaling(x_train_fsel, x_test_fsel)

    # Prepare results dataframe
    # results = np.zeros((x_test_fsel.shape[0], 2))
    # results[:, 0] = list(range(x_test_fsel.shape[0]))
    # results[:, 1] = y_predict

    # Save the output weights
    # if not ospath.exists(output_pathname):
    #     makedirs(output_pathname)
    # np.savetxt(output_filepath, results, fmt=["%1.1f", "%1.1f"], newline="\n", delimiter=",", header="id,y",
    #            comments="")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process sleep data")
    parser.add_argument("--debug", action='store_true')
    parser.add_argument("--outfile", required=False, default="out.csv")
    args = parser.parse_args()

    main(debug=args.debug, outfile=args.outfile)