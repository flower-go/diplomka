from text_classification_dataset import TextClassificationDataset
import tensorflow_datasets as tfds
import pandas as pd

# loading csfd and mall datset from: https://github.com/kysely/sentiment-analysis-czech/blob/sentence-level/Sentiment%20Analysis%20in%20Czech.ipynb

class SentimentDataset():


    def __init__(self, tokenizer):

        #TODO dodat spravne cesty k
        self.labels = {'n': 1, '0': 0, 'p': 2, 'b': 'BIP'}
        self.target_labels = [self.labels['n'], self.labels['0'], self.labels['p']]
        self.max_sentence_length = 30  # no. of words
        self.tokenizer = tokenizer


    def get_dataset(self, dataset_name, path=None):
        if dataset_name == "facebook":
            return TextClassificationDataset(path + "/" + "czech_facebook", tokenizer=self.tokenizer.encode)
        if dataset_name == "imdb":
            return self._return_imdb(self.tokenizer)
        if dataset_name == "csfd":
            path = path + "/" + dataset_name
            return self.load_data(path)
        if dataset_name == "mall":
            path = path + "/" + dataset_name + "cz"
            return self.load_data(path)

    def _return_imdb(self, tokenizer):


        train_data, train_labels = tfds.load(name="imdb_reviews", split="train",
                                      batch_size=-1, as_supervised=True)

        train_examples = tfds.as_numpy(train_data)
        train_examples = self._imdb_covertion(train_examples, tokenizer)


        return train_examples, train_labels

    def _imdb_covertion(self,data,tokenizer):
        for i in range(len(data)):

            if len(data[i]) > 512:
                data[i] = data[i][0:512]

            data[i] = tokenizer.encode(data[i].decode('latin1'))
        return data

    def load_gold_data(self,directory, filter_out):
        '''
        Loads a dataset with separate contents and labels. Maps labels to our format.
        Filters out any samples that have a label equal to the second argument.

        Returns a new DataFrame.
        '''
        return pd \
            .concat([
            pd.read_csv('data/{}/gold-posts.txt'.format(directory), sep='\n', header=None, names=['Post']),
            pd.read_csv('data/{}/gold-labels.txt'.format(directory), sep=' ', header=None, names=['Sentiment']).iloc[:,
            0].map(self.labels)
        ], axis=1) \
            .query('Sentiment != @filter_out') \
            .reset_index(drop=True)

    def load_data(self,directory):
        '''
        Loads a dataset whose samples are split to individual files/per class.

        Returns a new DataFrame.
        '''
        return pd \
            .concat([
            pd.read_csv('{}/positive.txt'.format(directory), sep='\n', header=None, names=['Post']).assign(
                Sentiment=self.labels['p']),
            pd.read_csv('{}/neutral.txt'.format(directory), sep='\n', header=None, names=['Post']).assign(
                Sentiment=self.labels['0']),
            pd.read_csv('{}/negative.txt'.format(directory), sep='\n', header=None, names=['Post']).assign(
                Sentiment=self.labels['n'])
        ], axis=0) \
            .reset_index(drop=True)
