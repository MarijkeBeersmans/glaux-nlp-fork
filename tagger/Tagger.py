# -*- coding: UTF-8 -*-

from data.CONLLReader import CONLLReader
from classification.Classifier import Classifier
from tokenization import Tokenization
import unicodedata as ud
import multiprocessing
from functools import partial
import os
from transformers import AutoConfig
from argparse import ArgumentParser
from lexicon import LexiconProcessor

class Tagger:

    def __init__(self, transformer_path, tokenizer_path, model_dir, training_data=None, test_data=None, lexicon_file=None, possible_tags_file=None, data_preset='UD', feats=['UPOS','XPOS','FEATS'], unknown_label=None, all_tag_combinations=False, add_training_data_to_lexicon=True,normalization_rule=None):
        self.reader = CONLLReader(preset=data_preset)
        self.transformer_path = transformer_path
        self.tokenizer_path = tokenizer_path
        self.feats = feats
        self.model_dir = model_dir
        self.unknown_label = unknown_label
        if training_data is not None:
            self.training_data = self.reader.parse_conll(training_data)
        else:
            self.training_data = None
        if test_data is not None:
            self.test_data = self.reader.parse_conll(test_data)
        else:
            self.test_data = None
        self.all_tag_combinations= all_tag_combinations
        self.feature_dict = self.build_feature_dict()
        if lexicon_file is not None:
            self.lexicon = self.read_lexicon(lexicon_file)
            self.trim_lexicon()
            if add_training_data_to_lexicon and self.training_data is not None:
                lp = LexiconProcessor(self.lexicon)
                lp.add_data(self.training_data,feats=self.feature_dict,col_token=self.reader.feature_cols['FORM'],col_upos=self.reader.feature_cols['UPOS'],col_xpos=self.reader.feature_cols['XPOS'],col_morph=self.reader.feature_cols['FEATS'],normalization_rule=normalization_rule)
        else:
            self.lexicon = None
        self.possible_tags = self.build_possible_tags(possible_tags_file)
        self.normalization_rule = normalization_rule

    
    def tag_individual_feat(self,feat,wids,tokens):
        feature_classifier = Classifier(self.transformer_path,tokenizer_path=self.tokenizer_path,unknown_label=self.unknown_label,model_dir=self.model_dir)
        if self.test_data is not None:
            if feat == 'UPOS' or feat == 'XPOS':
                tags = self.reader.read_tags(feat, self.test_data, return_words=False)
            else:
                tags = self.reader.read_tags(feat, self.test_data, in_feats=True, return_words=False)
        else:
            tags = []
            for sent in tokens:
                tag_sent = []
                for word in sent:
                    tag_sent.append(self.unknown_label)
                tags.append(tag_sent)
        preds = feature_classifier.predict(tokens,tags,wids,model_dir=f"{self.model_dir}/{feat}")
        return (feat, preds)
    
    def tag_seperately(self,wids,tokens,multicore=False):
        all_preds = {}
        if multicore:
            pool = multiprocessing.Pool()
            func = partial(self.tag_individual_feat, wids=wids, tokens=tokens)
            results = pool.map(func, self.feature_dict)
            for result in results:
                all_preds[result[0]] = result[1]
        else:
            for feat in self.feature_dict:
                result = self.tag_individual_feat(feat,wids,tokens)
                all_preds[result[0]] = result[1]
                print('Predicted '+feat)
        return all_preds
    
    def train_individual_feat(self,feat,batch_size,epochs,normalization_rule=None):
        feat_classifier = Classifier(transformer_path=self.transformer_path,model_dir=self.model_dir,tokenizer_path=self.tokenizer_path)
        if feat=='UPOS' or feat=='XPOS':
            wids, tokens, tags = self.reader.read_tags(feat,self.training_data)
        else:
            wids, tokens, tags = self.reader.read_tags(feat,self.training_data,in_feats=True)
        tokens_norm = tokens
        if normalization_rule is not None:
            tokens_norm = Tokenization.normalize_tokens(tokens, normalization_rule)
        feat_classifier.train_classifier(wids, tokens_norm, tags, output_model=f"{self.model_dir}/{feat}",batch_size=batch_size,epochs=epochs)
        
    def train_separate_models(self,batch_size=16,epochs=3,multicore=False,normalization_rule=None):
        if multicore:
            pool = multiprocessing.Pool()
            func = partial(self.train_individual_feat, batch_size=batch_size, epochs=epochs,normalization_rule=normalization_rule)
            pool.map(func, self.feature_dict)        
        else:
            for feat in self.feature_dict:
                self.train_individual_feat(feat,batch_size,epochs,normalization_rule=normalization_rule)
        
    def build_feature_dict(self):
        # Builds a dictionary with all tagging features and their possible values, based on the training data or the saved tagger models. This might be unnecessary: it is not clear to me anymore why we need the possible_values instead of only the names of the features. Maybe just for diagnostic purposes?
        feature_dict = dict()
        if self.training_data is not None:
            for sent in self.training_data:
                for line in sent.split('\n'):
                    split = line.rstrip().split('\t')
                    if 'UPOS' in self.feats:
                        current_upos = split[self.reader.feature_cols['UPOS']]
                        if 'UPOS' in feature_dict:
                            upos_values = feature_dict['UPOS']
                            upos_values.add(current_upos)
                        else:
                            upos_values = set()
                            upos_values.add(current_upos)
                            feature_dict['UPOS'] = upos_values
                    if 'XPOS':
                        current_xpos = split[self.reader.feature_cols['XPOS']]
                        if 'XPOS' in feature_dict:
                            xpos_values = feature_dict['XPOS']
                            xpos_values.add(current_xpos)
                        else:
                            xpos_values = set()
                            xpos_values.add(current_xpos)
                            feature_dict['XPOS'] = xpos_values
                    if split[self.reader.feature_cols['FEATS']] != '_':
                        morph = split[self.reader.feature_cols['FEATS']].split('|')
                        for feat in morph:
                            split_feat = feat.split('=')
                            if 'FEATS' in self.feats or split_feat[0] in self.feats:
                                if split_feat[0] in feature_dict:
                                    feat_values = feature_dict[split_feat[0]]
                                    feat_values.add(split_feat[1])
                                else:
                                    feat_values = set()
                                    feat_values.add(split_feat[1])
                                    feature_dict[split_feat[0]] = feat_values
        elif self.model_dir is not None:
            for file in os.listdir(self.model_dir):
                path = os.path.join(self.model_dir,file)
                if os.path.isdir(path):
                    config = AutoConfig.from_pretrained(path)
                    feature_dict[file] = set(config.label2id.keys())
        return feature_dict
    
    def build_possible_tags(self,possible_tags_file):
        possible_tags = []
        if possible_tags_file is not None:
            file = open(possible_tags_file, encoding='utf-8')
            raw_text = file.read().strip()
            lines = raw_text.split('\n')
            header = lines.pop(0).split('\t')
            feat_col = {}
            for col, feat in enumerate(header):
                feat_col[feat] = col
            for line in lines:
                tag = []
                vals = line.split('\t')
                for feat in self.feature_dict:
                    tag.append((feat, vals[feat_col[feat]]))
                tag = tuple(tag)
                possible_tags.append(tag)
        elif self.training_data is not None and not self.all_tag_combinations:
            for sent in self.training_data:
                for line in sent.split('\n'):
                    split = line.rstrip().split('\t')
                    tag = []
                    if 'FEATS' in self.reader.feature_cols:
                        feats = split[self.reader.feature_cols['FEATS']]
                        feats_split = feats.split('|')
                    for feat in self.feature_dict:
                        if feat == 'UPOS':
                            tag.append((feat,split[self.reader.feature_cols['UPOS']]))
                        elif feat == 'XPOS':
                            tag.append((feat,split[self.reader.feature_cols['XPOS']]))
                        else:
                            if feats == '_':
                                tag.append((feat,'_'))
                            else:
                                found = False
                                for feat_val in feats_split:
                                    feat_val_split = feat_val.split('=')
                                    if feat_val_split[0] == feat:
                                        tag.append((feat_val_split[0],feat_val_split[1]))
                                        found = True
                                        break
                                if not found:
                                    tag.append((feat,'_'))
                    tag = tuple(tag)
                    if not tag in possible_tags:
                        possible_tags.append(tag)
        else:
            return None
        return possible_tags


    def calc_tag_probs(self,possible_tags,preds,word_no):
        tag_probs = {}
        for tag in possible_tags:
            tag_prob = 1
            for feat in tag:
                try:
                    prob_attr = preds[feat[0]][word_no][feat[1]]
                except KeyError:
                    print('Feature not found (probably mismatch with lexicon): '+feat[0]+' '+feat[1])
                except IndexError:
                    print(word_no)
                    print(len(preds[feat[0]]))
                tag_prob *= prob_attr
            tag_probs[tag] = tag_prob
        tag_probs = sorted(tag_probs.items(), reverse=True, key=lambda x: x[1])
        return tag_probs
    
    def tag_data(self, tokens, preds, return_all_probs=False, return_num_poss=False):
        best_tags = []
        all_tags = []
        num_poss = []
        word_no = -1
        for sent in tokens:
            for word in sent:
                word_no += 1
                possible_tags = self.possible_tags
                if self.lexicon is not None and word in self.lexicon:
                    possible_tags = self.lexicon[word]
                if possible_tags is not None:
                    tag_probs = self.calc_tag_probs(possible_tags,preds,word_no)
                    top_prediction = tag_probs[0]
                    best_tags.append(top_prediction)
                    if return_all_probs:
                        all_tags.append(tag_probs)
                    if return_num_poss:
                        num_poss.append(len(possible_tags))
                else:
                    tag = []
                    prob = 1
                    #For now, we don't calculate the probability of all combinations if no list of possible tags is supplied, meaning all_tags will be empty
                    for feat in preds:
                        poss = sorted(preds[feat][word_no].items(), reverse=True, key=lambda x:x[1])
                        best_poss = poss[0]
                        tag.append((feat,best_poss[0]))
                        prob = prob * best_poss[1]
                    tag = tuple(tag)
                    best_tags.append((tag,prob))
                    num_poss.append('NA')
        if return_all_probs and return_num_poss:
            return best_tags, all_tags, num_poss
        elif return_num_poss:
            return best_tags, num_poss
        elif return_all_probs:
            return best_tags, all_tags
        else:
            return all_tags
        
    def write_prediction(self,wids,tokens,tokens_norm,best_tags,output_file,output_format='CONLL',num_poss=None):
        with open(output_file, 'w', encoding='UTF-8') as outfile:
            if output_format == 'tab':
                outfile.write("id\ttoken\tprobability\tpossibilities\tin_lexicon")
                for feat in self.feature_dict:
                    outfile.write("\t"+feat)
                outfile.write('\n')
            word_no = -1
            for sent_id, sent in enumerate(tokens):
                for word_id, word in enumerate(sent):
                    wid = wids[sent_id][word_id]
                    word_no += 1
                    top_prediction = best_tags[word_no]
                    tag = dict(top_prediction[0])
                    upos = "_"
                    xpos = "_"
                    if 'UPOS' in self.feats:
                        upos = tag["UPOS"]
                    if 'XPOS' in self.feats:
                        xpos = tag["XPOS"]
                    if output_format == 'CONLL':
                        morph = ""
                        for feat, val in tag.items():
                            if feat != 'UPOS' and feat != 'XPOS' and val != '_':
                                morph += feat + "=" + val + "|"
                        if morph == "":
                            morph = '_'
                        else:
                            morph = morph[:-1]
                        outfile.write(wid + "\t" + word + "\t_\t" + upos + "\t" + xpos + "\t" + morph + "\t_\t_\t_\t_\n")
                    elif output_format == 'tab':
                        if self.lexicon is not None:
                            word_norm = tokens_norm[sent_id][word_id]
                            outfile.write(wid +"\t" + word + f"\t{top_prediction[1]:.5f}\t" + str(num_poss[word_no]) + "\t" + str(word_norm in self.lexicon))
                        else:
                            outfile.write(wid +"\t" + word + f"\t{top_prediction[1]:.5f}\t" + str(num_poss[word_no]) + "\tNA")
                        for feat in self.feature_dict:
                            val = '_'
                            if feat in tag:
                                val = tag[feat]
                            outfile.write("\t"+val)
                        outfile.write('\n')
                if output_format == 'CONLL':
                    outfile.write('\n')
    
    def prediction_string(self,tokens,wids,all_tags):
        word_no = -1
        output = ""
        for sent_id, sent in enumerate(tokens):
            for word_id, word in enumerate(sent):
                    wid = wids[sent_id][word_id]
                    word_no += 1
                    tag_probs = all_tags[word_no]
                    top_prediction = tag_probs[0]
                    tag = dict(top_prediction[0])
                    second_tag = {}
                    if len(tag_probs)>1:
                        second_prediction = tag_probs[1]
                        second_tag = dict(second_prediction[0])
                    output+=('{0:.3f}'.format(top_prediction[1])+'&nbsp;&nbsp;&nbsp;&nbsp;'+'<b><font style="'+self.color_by_prob(top_prediction[1])+'">'+word+'</font></b>'+'&nbsp;&nbsp;&nbsp;&nbsp;'+str(word in self.lexicon)+'&nbsp;&nbsp;&nbsp;&nbsp;'+str(tag)+'&nbsp;&nbsp;&nbsp;&nbsp;'+str(second_tag)+'<br>')
        return output

    def read_lexicon(self, file):
        lexicon = {}
        file = open(file, encoding='utf-8')
        raw_text = file.read().strip()
        lines = raw_text.split('\n')
        header = lines.pop(0).split('\t')
        feat_col = {}
        for col, feat in enumerate(header):
            feat_col[feat] = col
        for line in lines:
            entry = line.split('\t')
            form = entry[0]
            tag = []
            for feat in self.feature_dict:
                tag.append((feat, entry[feat_col[feat]]))
            tag = tuple(tag)
            if form in lexicon:
                tags = lexicon[form]
                if tag not in tags:
                    tags.append(tag)
            else:
                tags = []
                tags.append(tag)
                lexicon[form] = tags
        return lexicon
    
    def trim_lexicon(self):
        # Removes unnecessary information and sorts the tag in the order of the feature dict
        lexicon_new = {}
        for form, tags in self.lexicon.items():
            new_tags = []
            for tag in tags:
                new_tag = []
                for feat in self.feature_dict:
                    for tag_feat in tag:
                        if tag_feat[0] == feat:
                            new_tag.append(tag_feat)
                new_tag = tuple(new_tag)
                new_tags.append(new_tag)
            lexicon_new[form] = new_tags
        self.lexicon = lexicon_new
        
    def read_string(self, string, lang='greek_glaux'):
        if lang=='greek_glaux':
            tokens_str = Tokenization.greek_glaux_to_tokens(string)
        else:
            tokens_str = string.split(' ')
        tokens = []
        current_sent = []
        new_sent = False
        for token in tokens_str:
            if lang=='greek_glaux':
                token = ud.normalize("NFKD",token)
                token = Tokenization.normalize_greek_punctuation(token)
            if new_sent:
                tokens.append(current_sent)
                current_sent = []
                new_sent = False
            current_sent.append(token)
            if lang=='greek_glaux' or lang=='greek':
                if token=='.' or token==';' or token == '·':
                    new_sent = True
            else:
                if token=='.' or token=='?' or token=='!':
                    new_sent = True
        tokens.append(current_sent)
        wids = []
        for sent in tokens:
            wids_sent = []
            for id, token in enumerate(sent):
                wids_sent.append(str(id+1))
            wids.append(wids_sent)
        wids = []
        wids.append(wids_sent)
        return wids, tokens

    def color_by_prob(self,prob):
        green = 200*prob
        red = 200*(1-prob)
        return "color: rgb("+f'{red:.0f}'+","+f'{green:.0f}'+",0)";
    
if __name__ == '__main__':
    arg_parser = ArgumentParser()
    arg_parser.add_argument('mode',help='train/test')
    arg_parser.add_argument('transformer_path',help='path to the transformer model')
    arg_parser.add_argument('model_dir',help='path of tagging models')
    arg_parser.add_argument('--feats',help='features to be extracted from the CONLL, default UPOS,XPOS,FEATS (any specific values in the FEATS column are also possible)',default='UPOS,XPOS,FEATS')
    arg_parser.add_argument('--tokenizer_path',help='path to the tokenizer (defaults to the path of the transformer model)')
    arg_parser.add_argument('--training_data',help='tagger training data')
    arg_parser.add_argument('--test_data',help='tagger test data')
    arg_parser.add_argument('--output_file',help='tagged data')
    arg_parser.add_argument('--output_format',help='format of the output data: CONLL (standard CONLL, with prediction in MISC) or tab (tabular format, with tag probability, number possible tags, whether the tag occurs in the lexicon, and without sentence boundaries)',default='CONLL')
    arg_parser.add_argument('--unknown_label',help='tag in the test data for tokens for which we do not know the label beforehand')
    arg_parser.add_argument('--possible_tags_file',help='file containing all possible morphology combinations that are linguistically valid')
    arg_parser.add_argument('--lexicon',help='file containing morphological lexicon')
    arg_parser.add_argument('--normalization_rule',help='normalize tokens during training/testing, normalization rules implemented are greek_glaux and standard NFD/NFKD/NFC/NFKC')
    arg_parser.add_argument('--epochs',help='number of epochs for training, defaults to 3',type=int,default=3)
    arg_parser.add_argument('--batch_size',help='batch size for training/testing, defaults to 16',type=int,default=16)
    arg_parser.add_argument('--multicore',help='whether to train multicore or not',action="store_true")
    args = arg_parser.parse_args()
    feats = None
    if args.feats is not None:
        feats = args.feats.split(',')
    if args.mode == 'train':
        if args.training_data == None:
            print('Training data is missing')
        else:
            tagger = Tagger(training_data=args.training_data,tokenizer_path=args.tokenizer_path,transformer_path=args.transformer_path,feats=feats,model_dir=args.model_dir)
            tagger.train_separate_models(batch_size=args.batch_size,epochs=args.epochs,multicore=args.multicore,normalization_rule=args.normalization_rule)
    elif args.mode == 'test':
        if args.test_data == None:
            print('Test data is missing')
        else:
            tagger = Tagger(training_data=args.training_data,test_data=args.test_data,tokenizer_path=args.tokenizer_path,transformer_path=args.transformer_path,feats=feats,model_dir=args.model_dir,unknown_label=args.unknown_label,lexicon_file=args.lexicon,possible_tags_file=args.possible_tags_file)
            wids, tokens = tagger.reader.read_tags(data=tagger.test_data, feature=None, return_tags=False)
            tokens_norm = tokens
            if args.normalization_rule is not None:
                tokens_norm = Tokenization.normalize_tokens(tokens, args.normalization_rule)
            all_preds = tagger.tag_seperately(wids, tokens_norm, args.multicore)
            best_tags, num_poss = tagger.tag_data(tokens_norm,all_preds,False,True)
            if args.output_file is not None:
                tagger.write_prediction(wids, tokens, tokens_norm, best_tags, args.output_file, args.output_format, num_poss)
    