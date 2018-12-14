import argparse
import torch
import torch.optim as optim
import torch.nn as nn
import torch.nn.functional as F
import pandas as pd
import json
from datetime import datetime
from torchtext import vocab
import sys
from hyperopt import hp, tpe, fmin, space_eval, Trials
from hyperopt.mongoexp import MongoTrials
from pprint import pprint

from src.ReutersDataset import ReutersDataset
from src.ArttuModels import CNN
from src.performance_measures import calculate_f1_score, pAtK
from src.gridsearch_util import load_training_set_as_df, get_loaders, train, validate


DF_FILEPATH = 'train/train.json.xz'
LOG_FP = 'model_stats_hyperopt_ARTTU_CNN_181214.json'
BATCH_SIZE = 256
NUM_WORKERS = 15
EPOCHS = 20
NO_OF_EVALS = 200


def grid_search(cpu_mode=False, gpu_no=0):

    space = {
        "glove_dim": hp.choice("glove_dim", [50, 100]),
        "num_filters": hp.quniform("num_filters", 300, 800, 1.0),
        "filter_sizes": hp.choice("filter_sizes", [[3, 4, 5], [1, 3, 5], [1, 4, 7]]),
        "compact_dim": hp.quniform("compact_dim", 50, 300, 1.0),
        "dropout_pctg": hp.uniform("dropout_pctg", 0.01, 0.5),
        "stride": hp.choice("stride", [1, 2]),
        "txt_length": hp.quniform("txt_length", 500, 3000, 1.0),
        "gpu_no": gpu_no,
        "cpu_mode": cpu_mode
    }

    trials = Trials()

    best = fmin(fn=train_model, space=space,
                algo=tpe.suggest, max_evals=1000, trials=trials)


def train_model(
        train_params):

    glove_dim = train_params['glove_dim']
    num_filters = int(train_params['num_filters'])
    filter_sizes = train_params['filter_sizes']
    compact_dim = int(train_params['compact_dim'])
    dropout = round(train_params['dropout_pctg'], 2)
    stride = train_params['stride']
    txt_length = int(train_params['txt_length'])
    gpu_no = train_params['gpu_no']
    cpu_mode = train_params['cpu_mode']

    # Initialize log for training session
    try:
        with open(LOG_FP, "r") as file:
            model_stats = json.load(file)
    except Exception as e:
        model_stats = {}

    train_session_name = json.dumps(train_params)

    model_stats[train_session_name] = {
        "glove_dim": glove_dim,
        "num_filters": num_filters,
        "filter_sizes": filter_sizes,
        "compact_dim": compact_dim,
        "dropout": dropout,
        "stride": stride,
        "txt_length": txt_length,
        "glove_dim ": glove_dim,
        "batch_size": BATCH_SIZE,
        "num_workers": NUM_WORKERS,
        "epochs": EPOCHS,
        'train_start': str(datetime.now()),
    }

    device = fetch_device(cpu_mode, gpu_no)

    loss_vector = None
    try:
        glove = vocab.GloVe(name="6B", dim=glove_dim)

        model = CNN(glove, num_filters, filter_sizes,
                    compact_dim, dropout, stride)

        model = model.to(device)

        df = load_training_set_as_df(DF_FILEPATH)
        train_loader, test_loader = get_loaders(
            df, BATCH_SIZE, NUM_WORKERS, txt_length, glove)

        # Train params
        criterion = nn.BCEWithLogitsLoss()
        parameters = model.parameters()
        optimizer = optim.Adam(parameters)

        print(f"Starting training: {train_session_name}")
        train_vector, loss_vector = [], []

        true_epochs = 0
        for epoch in range(1, EPOCHS + 1):
            true_epochs += 1
            print(f'Training epoch no {epoch}')
            train(device, model, epoch, train_loader, optimizer,
                  criterion, train_vector, logs_per_epoch=7)
            validate(device, model, test_loader, criterion, loss_vector)

            # Make an early quit if the loss is not improving
            if loss_vector.index(min(loss_vector)) < len(loss_vector) - 3:
                print('Making an early quit since loss is not improving')
                break

        f1_score_2 = calculate_f1_score(
            device, model, test_loader, 2, BATCH_SIZE)
        f1_score_3 = calculate_f1_score(
            device, model, test_loader, 3, BATCH_SIZE)
        f1_score_4 = calculate_f1_score(
            device, model, test_loader, 4, BATCH_SIZE)

        pAtK_1 = pAtK(device, model, test_loader, 1, BATCH_SIZE)
        pAtK_3 = pAtK(device, model, test_loader, 3, BATCH_SIZE)
        pAtK_5 = pAtK(device, model, test_loader, 5, BATCH_SIZE)

        model_stats[train_session_name]['f1_scores'] = {
            "f1_score_2": f1_score_2,
            "f1_score_3": f1_score_3,
            "f1_score_4": f1_score_4,
        }

        model_stats[train_session_name]['pAtK_scores'] = {
            "pAtK_1": pAtK_1,
            "pAtK_3": pAtK_3,
            "pAtK_5": pAtK_5,
        }

        model_stats[train_session_name]["train_vector"] = train_vector
        model_stats[train_session_name]["loss_vector"] = loss_vector
        model_stats[train_session_name]["epochs"] = true_epochs

    except Exception as e:
        train_error_message = str(e)
        model_stats[train_session_name]['train_error_message'] = train_error_message

    model_stats[train_session_name]['train_finish'] = str(datetime.now())
    model_stats[train_session_name]["model"] = str(model)

    with open(LOG_FP, "w") as file:
        json.dump(model_stats, file)

    if loss_vector:
        return min(loss_vector)

    else:
        return 1.0


if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument('-g', '--gpu_no', type=int)
    parser.add_argument('-c', '--cpu_mode', action='store_true')

    args = parser.parse_args()

    if args.cpu_mode:
        grid_search(cpu_mode=args.cpu_mode)

    if not args.gpu_no and args.gpu_no != 0:
        print('Please provide GPU # or use CPU')
        sys.exit(1)

    grid_search(gpu_no=args.gpu_no)