#!/usr/bin/env python3
import collections
import json

import numpy as np
import tensorflow as tf
import tensorflow_addons as tfa
import morpho_dataset

class Network:
    def __init__(self, args, num_words, num_chars, factor_words):

        def create_metrics(factors_arg):
            result = []
            for factors in [["Lemmas"], ["Tags"], ["Lemmas", "Tags"]]:
                for use_dict in ["Raw", "Dict"]:
                    if all(factor in factors_arg for factor in factors):
                        result.append("".join(factors) + use_dict)
            return result

        self.METRICS = create_metrics(args.factors)


        # TODO .Model

        # inputs placeholders, not needed
        word_ids = tf.keras.layers.Input(shape=[None], dtype=tf.int32)
        charseq_ids = tf.keras.layers.Input(shape=[None], dtype=tf.int32)
        charseqs = tf.keras.layers.Input(shape=[None], dtype=tf.int32)
        if args.embeddings:
            embeddings = tf.keras.layers.Input(shape=[None, args.embeddings_size], dtype=tf.float32)
        if args.elmo_size:
            elmo = tf.keras(shape=[None, args.elmo_size], dtype=tf.float32)


         # INPUTS - create all embeddings
        inputs = []
        if args.we_dim:
             inputs.append(tf.keras.layers.Embedding(num_words, args.we_dim, mask_zero=True)(word_ids))

        cle = tf.keras.layers.Embedding(num_chars, args.cle_dim, mask_zero=True)(charseqs)
        cle = tf.keras.layers.Dropout(rate=args.dropout)(cle)
        cle = tf.keras.layers.Bidirectional(tf.keras.layers.GRU(args.cle_dim), merge_mode="concat")(cle)
        #cle = tf.keras.layers.Lambda(lambda args: tf.gather(*args))([cle, charseq_ids])
        cle = tf.gather(cle,charseq_ids)

        # If CLE dim is half WE dim, we add them together, which gives
        # better results; otherwise we concatenate CLE and WE.
        if 2 * args.cle_dim == args.we_dim:
            inputs[-1] = tf.keras.layers.Add()([inputs[-1], cle])
        else:
            inputs.append(cle)

        # Pretrained embeddings
        if args.embeddings:
            inputs.append(embeddings)

         # Contextualized embeddings
        if args.elmo_size:
            inputs.append(elmo)

        hidden = tf.keras.layers.Concatenate()(inputs)

            # RNN cells

        hidden = tf.keras.layers.Dropout(rate=args.dropout)(hidden)
        # TODO dávat tam inputs ANO to je jen řetězení z předchozí vrstvy
        for i in range(args.rnn_layers):
            previous = hidden
            rnn_layer = getattr(tf.keras.layers, args.rnn_cell)(args.rnn_cell_dim, return_sequences=True)
            hidden = tf.keras.layers.Bidirectional(rnn_layer, merge_mode="sum")(hidden)
            hidden = tf.keras.layers.Dropout(rate=args.dropout)(hidden)
            if i:
                hidden = tf.keras.layers.Add()([previous, hidden])


        # tagger
        outputs = []
        for factor in args.factors:
            factor_layer = hidden
            for _ in range(args.factor_layers):
                factor_layer = tf.keras.layers.Add()([factor_layer,tf.keras.layers.Dropout(rate=args.dropout)(
                    tf.keras.layers.Dense(args.rnn_cell_dim, activation=tf.nn.tanh)(factor_layer))])
            if factor == "Lemmas":
                factor_layer = tf.keras.layers.Concatenate()([factor_layer, cle])
            outputs.append(tf.keras.layers.Dense(factor_words[factor], activation=tf.nn.softmax)(factor_layer))

        self.model = tf.keras.Model(inputs=[word_ids, charseq_ids, charseqs], outputs=outputs)

        #TODO lazy adam
        self._optimizer = tf.optimizers.Adam()
        self._writer = tf.summary.create_file_writer(args.logdir, flush_millis=10 * 1000)

    @tf.function(input_signature=[[tf.TensorSpec(shape=[None, None], dtype=tf.int32)] * 3,
                                  tf.TensorSpec(shape=[None, None, None], dtype=tf.int32)])
    def train_batch(self, inputs, factors):
        tags_mask = tf.not_equal(factors[0], 0)
        with tf.GradientTape() as tape:
            probabilities = self.model(inputs, training=True)
            loss = self._loss(tags, probabilities, tags_mask)
        gradients = tape.gradient(loss, self.model.variables)
        self._optimizer.apply_gradients(zip(gradients, self.model.variables))

        tf.summary.experimental.set_step(self._optimizer.iterations)
        with self._writer.as_default():
            for name, metric in self._metrics.items():
                metric.reset_states()
                if name == "loss": metric(loss)
                else: metric(tags, probabilities, tags_mask)
                tf.summary.scalar("train/{}".format(name), metric.result())

    def train_epoch(self, dataset, args):
        for batch in dataset.batches(args.batch_size):
            self.train_batch([batch[dataset.FORMS].word_ids, batch[dataset.FORMS].charseq_ids, batch[dataset.FORMS].charseqs],
                             batch[dataset.TAGS].word_ids)

    @tf.function(input_signature=[[tf.TensorSpec(shape=[None, None], dtype=tf.int32)] * 3,
                                  tf.TensorSpec(shape=[None, None], dtype=tf.int32)])
    def evaluate_batch(self, inputs, tags):
        tags_mask = tf.not_equal(tags, 0)
        probabilities = self.model(inputs, training=False)
        loss = self._loss(tags, probabilities, tags_mask)
        for name, metric in self._metrics.items():
            if name == "loss": metric(loss)
            else: metric(tags, probabilities, tags_mask)

    def evaluate(self, dataset, dataset_name, args):
        for metric in self._metrics.values():
            metric.reset_states()
        for batch in dataset.batches(args.batch_size):
            self.evaluate_batch([batch[dataset.FORMS].word_ids, batch[dataset.FORMS].charseq_ids, batch[dataset.FORMS].charseqs],
                                batch[dataset.TAGS].word_ids)

        metrics = {name: metric.result() for name, metric in self._metrics.items()}
        with self._writer.as_default():
            for name, value in metrics.items():
                tf.summary.scalar("{}/{}".format(dataset_name, name), value)

        return metrics



if __name__ == "__main__":




    import argparse
    import datetime
    import json
    import os
    import sys
    import re

    # Fix random seed
    np.random.seed(42)
    tf.random.set_seed(42)

    # command_line = " ".join(sys.argv[1:])
    #
    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("data", type=str, help="Input data")
    parser.add_argument("--batch_size", default=64, type=int, help="Batch size.")
    parser.add_argument("--beta_2", default=0.99, type=float, help="Adam beta 2")
    parser.add_argument("--char_dropout", default=0, type=float, help="Character dropout")
    parser.add_argument("--cle_dim", default=256, type=int, help="Character-level embedding dimension.")
    parser.add_argument("--dropout", default=0.5, type=float, help="Dropout")
    parser.add_argument("--elmo", default=None, type=str, help="External contextualized embeddings to use.")
    parser.add_argument("--embeddings", default=None, type=str, help="External embeddings to use.")
    parser.add_argument("--epochs", default="40:1e-3,20:1e-4", type=str, help="Epochs and learning rates.")
    parser.add_argument("--exp", default=None, type=str, help="Experiment name.")
    parser.add_argument("--factors", default="Lemmas,Tags", type=str, help="Factors to predict.")
    parser.add_argument("--factor_layers", default=1, type=int, help="Per-factor layers.")
    parser.add_argument("--label_smoothing", default=0.03, type=float, help="Label smoothing.")
    parser.add_argument("--lemma_re_strip", default=r"(?<=.)(?:`|_|-[^0-9]).*$", type=str, help="RE suffix to strip from lemma.")
    parser.add_argument("--lemma_rule_min", default=2, type=int, help="Minimum occurences to keep a lemma rule.")
    parser.add_argument("--min_epoch_batches", default=300, type=int, help="Minimum number of batches per epoch.")
    parser.add_argument("--predict", default=None, type=str, help="Predict using the passed model.")
    parser.add_argument("--rnn_cell", default="LSTM", type=str, help="RNN cell type.")
    parser.add_argument("--rnn_cell_dim", default=512, type=int, help="RNN cell dimension.")
    parser.add_argument("--rnn_layers", default=3, type=int, help="RNN layers.")
    parser.add_argument("--threads", default=4, type=int, help="Maximum number of threads to use.")
    parser.add_argument("--we_dim", default=512, type=int, help="Word embedding dimension.")
    parser.add_argument("--word_dropout", default=0.2, type=float, help="Word dropout")
    args = parser.parse_args()

    tf.config.threading.set_inter_op_parallelism_threads(args.threads)
    tf.config.threading.set_intra_op_parallelism_threads(args.threads)
    tf.config.set_soft_device_placement(True)
    #
    # if args.predict:
    #     # Load saved options from the model
    #     with open("{}/options.json".format(args.predict), mode="r") as options_file:
    #         args = argparse.Namespace(**json.load(options_file))
    #     parser.parse_args(namespace=args)
    # else:
    #     # Create logdir name
    #     if args.exp is None:
    #         args.exp = "{}-{}".format(os.path.basename(__file__), datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    #
    #     do_not_log = {"exp", "lemma_re_strip", "predict", "threads"}
    #     args.logdir = "models/{}-{}".format(
    #         args.exp,
    #         ",".join(("{}={}".format(re.sub("(.)[^_]*_?", r"\1", key), re.sub("[^,]*/", "", value) if type(value) == str else value)
    #                   for key, value in sorted(vars(args).items()) if key not in do_not_log))
    #     )
    #     if not os.path.exists("models"): os.mkdir("models")
    #     if not os.path.exists(args.logdir): os.mkdir(args.logdir)
    #
    #     # Dump passed options
    #     with open("{}/options.json".format(args.logdir), mode="w") as options_file:
    #         json.dump(vars(args), options_file, sort_keys=True)
    #
    # # Postprocess args
    # args.factors = args.factors.split(",")
    # args.epochs = [(int(epochs), float(lr)) for epochs, lr in (epochs_lr.split(":") for epochs_lr in args.epochs.split(","))]
    #
    # # Load embeddings
    # if args.embeddings:
    #     with np.load(args.embeddings, allow_pickle=True) as embeddings_npz:
    #         args.embeddings_words = embeddings_npz["words"]
    #         args.embeddings_data = embeddings_npz["embeddings"]
    #         args.embeddings_size = args.embeddings_data.shape[1]
    #
    # if args.predict:
    #     # Load training dataset maps from the checkpoint
    #     train = morpho_dataset.MorphoDataset.load_mappings("{}/mappings.pickle".format(args.predict))
    #     # Load input data
    #     predict = morpho_dataset.MorphoDataset(args.data, train=train, shuffle_batches=False, elmo=args.elmo)
    # else:
    #     # Load input data
    #     train = morpho_dataset.MorphoDataset("{}-train.txt".format(args.data),
    #                                          embeddings=args.embeddings_words if args.embeddings else None,
    #                                          elmo=re.sub("(?=,|$)", "-train.npz", args.elmo) if args.elmo else None,
    #                                          lemma_re_strip=args.lemma_re_strip,
    #                                          lemma_rule_min=args.lemma_rule_min)
    #     if os.path.exists("{}-dev.txt".format(args.data)):
    #         dev = morpho_dataset.MorphoDataset("{}-dev.txt".format(args.data), train=train, shuffle_batches=False,
    #                                            elmo=re.sub("(?=,|$)", "-dev.npz", args.elmo) if args.elmo else None)
    #     else:
    #         dev = None
    #
    #     if os.path.exists("{}-test.txt".format(args.data)):
    #         test = morpho_dataset.MorphoDataset("{}-test.txt".format(args.data), train=train, shuffle_batches=False,
    #                                            elmo=re.sub("(?=,|$)", "-test.npz", args.elmo) if args.elmo else None)
    #     else:
    #         test = None
    # args.elmo_size = train.elmo_size
    #
    # # Construct the network
    # network = Network(threads=args.threads)
    # network.construct(args=args,
    #                   num_words=len(train.factors[train.FORMS].words),
    #                   num_chars=len(train.factors[train.FORMS].alphabet),
    #                   factor_words=dict((factor, len(train.factors[train.FACTORS_MAP[factor]].words)) for factor in args.factors),
    #                   predict_only=args.predict)
    #
    # if args.predict:
    #     network.saver_inference.restore(network.session, "{}/checkpoint-inference".format(args.predict))
    #     network.predict(predict, sys.stdout, args)
    #
    # else:
    #     log_file = open("{}/log".format(args.logdir), "w")
    #     for factor in args.factors:
    #         print("{}: {}".format(factor, len(train.factors[train.FACTORS_MAP[factor]].words)), file=log_file, flush=True)
    #     print("Tagging with args:", "\n".join(("{}: {}".format(key, value) for key, value in sorted(vars(args).items())
    #                                            if key not in ["embeddings_data", "embeddings_words"])), flush=True)
    #
    #     for i, (epochs, learning_rate) in enumerate(args.epochs):
    #         for epoch in range(epochs):
    #             network.train_epoch(train, learning_rate, args)
    #
    #             if dev:
    #                 metrics = network.evaluate("dev", dev, args)
    #                 metrics_log = ", ".join(("{}: {:.2f}".format(metric, 100 * metrics[metric]) for metric in metrics))
    #                 for f in [sys.stderr, log_file]:
    #                     print("Dev, epoch {}, lr {}, {}".format(epoch + 1, learning_rate, metrics_log), file=f, flush=True)
    #
    #     network.saver_inference.save(network.session, "{}/checkpoint-inference".format(args.logdir), write_meta_graph=False)
    #     train.save_mappings("{}/mappings.pickle".format(args.logdir))
    #
    #     if test:
    #         metrics = network.evaluate("test", test, args)
    #         metrics_log = ", ".join(("{}: {:.2f}".format(metric, 100 * metrics[metric]) for metric in metrics))
    #         for f in [sys.stderr, log_file]:
    #             print("Test, epoch {}, lr {}, {}".format(epoch + 1, learning_rate, metrics_log), file=f, flush=True)
