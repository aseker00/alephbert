import torch.nn as nn
import torch


class TokenSeq2SeqMorphTagger(nn.Module):

    def __init__(self, char_emb, hidden_size, num_layers, enc_dropout, dec_dropout, out_size, out_dropout, num_labels):
        super().__init__()
        self.char_emb = char_emb
        self.enc_input_size = self.char_emb.embedding_dim
        self.enc_hidden_size = hidden_size
        self.enc_num_layers = num_layers
        self.enc_dropout = enc_dropout
        self.dec_input_size = self.char_emb.embedding_dim
        self.dec_hidden_size = hidden_size
        self.dec_num_layers = num_layers
        self.dec_dropout = dec_dropout
        self.out_size = out_size
        self.out_dropput = out_dropout
        self.num_labels = num_labels
        self.encoder = nn.GRU(input_size=self.enc_input_size,
                              hidden_size=self.enc_hidden_size,
                              num_layers=self.enc_num_layers,
                              bidirectional=False,
                              batch_first=False,
                              dropout=self.enc_dropout)
        self.decoder = nn.GRU(input_size=self.dec_input_size,
                              hidden_size=self.dec_hidden_size,
                              num_layers=self.dec_num_layers,
                              bidirectional=False,
                              batch_first=False,
                              dropout=self.dec_dropout)
        self.out = nn.Linear(in_features=self.dec_hidden_size, out_features=self.out_size)
        self.dropout = nn.Dropout(self.out_dropput)
        self.classifier = nn.Linear(in_features=self.dec_hidden_size, out_features=self.num_labels)

    def forward(self, in_token_char_seq, in_token_state, special_symbols, max_len, max_num_tags, target_char_seq, target_tag_seq):
        emb_chars = self.char_emb(in_token_char_seq).unsqueeze(1)
        # hidden = [n layers * n directions, batch size, hidden dim]
        enc_state = torch.split(in_token_state, in_token_state.shape[2] // self.enc_num_layers, dim=2)
        enc_state = torch.cat(enc_state, dim=0)
        enc_output, dec_state = self.encoder(emb_chars, enc_state)
        dec_char = special_symbols['<s>']
        dec_char_scores = []
        dec_label_scores = []
        while len(dec_char_scores) < max_len and len(dec_label_scores) < max_num_tags:
            emb_dec_char = self.char_emb(dec_char).unsqueeze(1)
            dec_output, dec_state = self.decoder(emb_dec_char, dec_state)
            dec_output = self.dropout(dec_output)
            dec_output = self.out(dec_output)
            if target_char_seq is not None:
                dec_char = target_char_seq[:, len(dec_char_scores), 0]
            else:
                dec_char = self.decode(dec_output).squeeze(0)
            dec_char_scores.append(dec_output)
            if torch.eq(dec_char, special_symbols['<sep>']):
                cur_seq_label_scores = self.classifier(dec_state[-1:])
                dec_label_scores.append(cur_seq_label_scores)
            if torch.all(torch.eq(dec_char, special_symbols['</s>'])):
                break
        if len(dec_label_scores) < max_num_tags:
            classifier_output = self.classifier(dec_state[-1:])
            dec_label_scores.append(classifier_output)
        fill_len = max_len - len(dec_char_scores)
        dec_char_scores.extend([dec_char_scores[-1]] * fill_len)
        fill_len = max_num_tags - len(dec_label_scores)
        dec_label_scores.extend([dec_label_scores[-1]] * fill_len)
        return torch.cat(dec_char_scores, dim=1), torch.cat(dec_label_scores, dim=1)

    def decode(self, label_scores):
        return torch.argmax(label_scores, dim=2)


class MorphTagModel(nn.Module):

    def __init__(self, xmodel, xtokenizer, char_emb, tagger):
        super().__init__()
        self.xmodel = xmodel
        self.xtokenizer = xtokenizer
        self.char_emb = char_emb
        self.tagger = tagger

    def forward(self, xtokens, tokens, special_symbols, max_form_len, max_tag_len, target_form_chars, target_tags):
        seg_scores = []
        tag_scores = []
        mask = xtokens != self.xtokenizer.pad_token_id
        token_ctx, sent_ctx = self.xmodel(xtokens, attention_mask=mask)
        cur_token_id = 1
        token_chars = tokens[tokens[:, :, 0] == cur_token_id]
        while token_chars.nelement() > 0:
            xtoken_ids = token_chars[:, 1]
            token_state = torch.mean(token_ctx[:, xtoken_ids], dim=1).unsqueeze(1)
            input_chars = token_chars[:, 2]
            target_char_seq = None
            if target_form_chars is not None:
                target_char_seq = target_form_chars[:, (cur_token_id - 1) * max_form_len:cur_token_id * max_form_len]
            target_tag_seq = None
            if target_tags is not None:
                target_tag_seq = target_tags[:, (cur_token_id - 1) * max_tag_len:cur_token_id * max_tag_len]
            seg_char_scores, seg_tag_scores = self.tagger(input_chars, token_state, special_symbols, max_form_len, max_tag_len, target_char_seq, target_tag_seq)
            seg_scores.append(seg_char_scores)
            tag_scores.append(seg_tag_scores)
            cur_token_id += 1
            token_chars = tokens[tokens[:, :, 0] == cur_token_id]
        seg_scores = torch.cat(seg_scores, dim=1)
        tag_scores = torch.cat(tag_scores, dim=1)
        return seg_scores, tag_scores

    def char_loss_prepare(self, dec_char_scores, gold_chars):
        return dec_char_scores[0], gold_chars[0, :, 0]

    def tag_loss_prepare(self, dec_tag_scores, gold_tags):
        return dec_tag_scores[0], gold_tags[0, :, 0]

    def decode(self, label_scores):
        return torch.argmax(label_scores, dim=2)
