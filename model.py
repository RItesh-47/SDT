import math
import torch
import torch.nn as nn
import torch.nn.functional as F
import math

'''/
Ritesh
changes made:
1. casual masking in transfromer encoder
2. casual convolution in Transformer encoder
3. introduced a new arugument modality ['atv', 'at', 'a', 't']
4. Transformer_Based_Model_diverse is the realtime model and Transformer_Based_Model is the  original one
/'''
class MaskedKLDivLoss(nn.Module):
    def __init__(self):
        super(MaskedKLDivLoss, self).__init__()
        self.loss = nn.KLDivLoss(reduction='sum')

    def forward(self, log_pred, target, mask):
        mask_ = mask.view(-1, 1)
        loss = self.loss(log_pred * mask_, target * mask_) / torch.sum(mask)   
        return loss


class MaskedNLLLoss(nn.Module):
    def __init__(self, weight=None):
        super(MaskedNLLLoss, self).__init__()
        self.weight = weight
        self.loss = nn.NLLLoss(weight=weight, reduction='sum')

    def forward(self, pred, target, mask):
        mask_ = mask.view(-1, 1)
        if type(self.weight) == type(None):
            loss = self.loss(pred * mask_, target) / torch.sum(mask)
        else:
            loss = self.loss(pred * mask_, target) \
                   / torch.sum(self.weight[target] * mask_.squeeze())  
        return loss

def gelu(x):
    return 0.5 * x * (1 + torch.tanh(math.sqrt(2 / math.pi) * (x + 0.044715 * torch.pow(x, 3))))

class PositionwiseFeedForward(nn.Module):
    def __init__(self, d_model, d_ff, dropout=0.1):
        super(PositionwiseFeedForward, self).__init__()
        self.w_1 = nn.Linear(d_model, d_ff)
        self.w_2 = nn.Linear(d_ff, d_model)
        self.layer_norm = nn.LayerNorm(d_model, eps=1e-6)
        self.actv = gelu
        self.dropout_1 = nn.Dropout(dropout)
        self.dropout_2 = nn.Dropout(dropout)

    def forward(self, x):
        inter = self.dropout_1(self.actv(self.w_1(self.layer_norm(x))))
        output = self.dropout_2(self.w_2(inter))
        return output + x


class MultiHeadedAttention(nn.Module):
    def __init__(self, head_count, model_dim, dropout=0.1):
        assert model_dim % head_count == 0
        self.dim_per_head = model_dim // head_count
        self.model_dim = model_dim

        super(MultiHeadedAttention, self).__init__()
        self.head_count = head_count

        self.linear_k = nn.Linear(model_dim, head_count * self.dim_per_head)
        self.linear_v = nn.Linear(model_dim, head_count * self.dim_per_head)
        self.linear_q = nn.Linear(model_dim, head_count * self.dim_per_head)
        self.softmax = nn.Softmax(dim=-1)
        self.dropout = nn.Dropout(dropout)
        self.linear = nn.Linear(model_dim, model_dim)

    def forward(self, key, value, query, mask=None):
        batch_size = key.size(0)
        dim_per_head = self.dim_per_head
        head_count = self.head_count

        def shape(x):
            """  projection """
            return x.view(batch_size, -1, head_count, dim_per_head).transpose(1, 2)

        def unshape(x):
            """  compute context """
            return x.transpose(1, 2).contiguous() \
                .view(batch_size, -1, head_count * dim_per_head)

        key = self.linear_k(key).view(batch_size, -1, head_count, dim_per_head).transpose(1, 2)
        value = self.linear_v(value).view(batch_size, -1, head_count, dim_per_head).transpose(1, 2)
        query = self.linear_q(query).view(batch_size, -1, head_count, dim_per_head).transpose(1, 2)

        query = query / math.sqrt(dim_per_head)
        scores = torch.matmul(query, key.transpose(2, 3))

        if mask is not None:
            # mask = mask.unsqueeze(1).unsqueeze(2)
            mask = mask.unsqueeze(1).expand_as(scores)
            scores = scores.masked_fill(mask, -1e10)

        attn = self.softmax(scores)
        drop_attn = self.dropout(attn)
        context = torch.matmul(drop_attn, value).transpose(1, 2).\
                    contiguous().view(batch_size, -1, head_count * dim_per_head)
        output = self.linear(context)
        return output


class PositionalEncoding(nn.Module):
    def __init__(self, dim, max_len=512):
        super(PositionalEncoding, self).__init__()
        pe = torch.zeros(max_len, dim)
        position = torch.arange(0, max_len).unsqueeze(1)
        div_term = torch.exp((torch.arange(0, dim, 2, dtype=torch.float) *
                              -(math.log(10000.0) / dim)))
        pe[:, 0::2] = torch.sin(position.float() * div_term)
        pe[:, 1::2] = torch.cos(position.float() * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)
    def forward(self, x, speaker_emb):
        L = x.size(1)
        pos_emb = self.pe[:, :L]
        x = x + pos_emb + speaker_emb
        return x


class TransformerEncoderLayer(nn.Module):
    def __init__(self, d_model, heads, d_ff, dropout):
        super(TransformerEncoderLayer, self).__init__()
        self.self_attn = MultiHeadedAttention(
            heads, d_model, dropout=dropout)
        self.feed_forward = PositionwiseFeedForward(d_model, d_ff, dropout)
        self.layer_norm = nn.LayerNorm(d_model, eps=1e-6)
        self.dropout = nn.Dropout(dropout)

    def forward(self, iter, inputs_a, inputs_b, mask, setting):
        if inputs_a.equal(inputs_b):
            if (iter != 0):
                inputs_b = self.layer_norm(inputs_b)
            else:
                inputs_b = inputs_b

            #Ritesh: just fixing dimensional inconsistencies that occured coz of casual masking
            if setting != 'realtime':
                mask = mask.unsqueeze(1)
            context = self.self_attn(inputs_b, inputs_b, inputs_b, mask=mask)
        else:
            if (iter != 0):
                inputs_b = self.layer_norm(inputs_b)
            else:
                inputs_b = inputs_b

            if setting != 'realtime':
                mask = mask.unsqueeze(1)
            context = self.self_attn(inputs_a, inputs_a, inputs_b, mask=mask)
        
        out = self.dropout(context) + inputs_b
        return self.feed_forward(out)
'''/
Ritesh 
Function for making casual mask
/'''
def generate_causal_mask(size, device):
    return torch.tril(torch.ones((size, size), dtype=torch.bool, device=device))


class TransformerEncoder(nn.Module):
    def __init__(self, d_model, d_ff, heads, layers, dropout=0.1):
        super(TransformerEncoder, self).__init__()
        self.d_model = d_model
        self.layers = layers
        self.pos_emb = PositionalEncoding(d_model)
        self.transformer_inter = nn.ModuleList(
            [TransformerEncoderLayer(d_model, heads, d_ff, dropout)
             for _ in range(layers)])
        self.dropout = nn.Dropout(dropout)

    '''/Ritesh
    have designed and applied a casual mask for each batch so that to attention is given only to past utterances 
    /'''
    def forward(self, x_a, x_b, mask, speaker_emb, setting):
        device = x_a.device
        batch_size, seq_len , _ = x_a.size()
        if setting =='realtime':
            causal_mask = generate_causal_mask(seq_len, device)
            padding_mask = mask.unsqueeze(1).to(device).bool()  # Shape: [batch_size, 1, seq_len]
            combined_mask = causal_mask.unsqueeze(0) & padding_mask  # Shape: [batch_size, seq_len, seq_len]
            inverted_mask = combined_mask.eq(0)
        else:
            inverted_mask = mask.eq(0)
        
        if x_a.equal(x_b):
            x_b = self.pos_emb(x_b, speaker_emb)
            x_b = self.dropout(x_b)
            for i in range(self.layers):
                x_b = self.transformer_inter[i](i, x_b, x_b, inverted_mask, setting)
        else:
            x_a = self.pos_emb(x_a, speaker_emb)
            x_a = self.dropout(x_a)
            x_b = self.pos_emb(x_b, speaker_emb)
            x_b = self.dropout(x_b)
            for i in range(self.layers):
                x_b = self.transformer_inter[i](i, x_a, x_b, inverted_mask, setting)
        return x_b


class Unimodal_GatedFusion(nn.Module):
    def __init__(self, hidden_size, dataset):
        super(Unimodal_GatedFusion, self).__init__()
        self.fc = nn.Linear(hidden_size, hidden_size, bias=False)
        if dataset == 'MELD':
            self.fc.weight.data.copy_(torch.eye(hidden_size, hidden_size))
            self.fc.weight.requires_grad = False

    def forward(self, a):
        z = torch.sigmoid(self.fc(a))
        final_rep = z * a
        return final_rep

'''/
Ritesh:
have made three Multimodal_GatedFusion functions used for different modalities
/'''

# for atv
class Multimodal_GatedFusion_three(nn.Module):
    def __init__(self, hidden_size):
        super(Multimodal_GatedFusion_three, self).__init__()
        self.fc = nn.Linear(hidden_size, hidden_size, bias=False)
        self.softmax = nn.Softmax(dim=-2)

    def forward(self, a, b, c):
        a_new = a.unsqueeze(-2)
        b_new = b.unsqueeze(-2)
        c_new = c.unsqueeze(-2)
        utters = torch.cat([a_new, b_new, c_new], dim=-2)
        utters_fc = torch.cat([self.fc(a).unsqueeze(-2), self.fc(b).unsqueeze(-2), self.fc(c).unsqueeze(-2)], dim=-2)  
        utters_softmax = self.softmax(utters_fc)
        utters_three_model = utters_softmax * utters        
        final_rep = torch.sum(utters_three_model, dim=-2, keepdim=False) 
        return final_rep

# for at
class Multimodal_GatedFusion_two(nn.Module):
    def __init__(self, hidden_size):
        super(Multimodal_GatedFusion_two, self).__init__()
        self.fc = nn.Linear(hidden_size, hidden_size, bias=False)
        self.softmax = nn.Softmax(dim=-2)

    def forward(self, a, b):
        a_new = a.unsqueeze(-2)
        b_new = b.unsqueeze(-2)
        utters = torch.cat([a_new, b_new], dim=-2)
        utters_fc = torch.cat([self.fc(a).unsqueeze(-2), self.fc(b).unsqueeze(-2)], dim=-2)  
        utters_softmax = self.softmax(utters_fc)
        utters_two_model = utters_softmax * utters        
        final_rep = torch.sum(utters_two_model, dim=-2, keepdim=False) 
        return final_rep

# for a, t
class Multimodal_GatedFusion_one(nn.Module):
    def __init__(self, hidden_size):
        super(Multimodal_GatedFusion_one, self).__init__()
        self.fc = nn.Linear(hidden_size, hidden_size, bias=False)
        self.softmax = nn.Softmax(dim=-2) 

    def forward(self, x):
        x_new = x.unsqueeze(-2)  
        x_fc = self.fc(x).unsqueeze(-2)
        x_softmax = self.softmax(x_fc)
        gated_output = x_softmax * x_new
        final_rep = torch.sum(gated_output, dim=-2, keepdim=False)
        return final_rep

'''/Ritesh
introduced casual convolution for that features from future utterances we dont consider future utterances in representation of any utterance
/'''
class CausalConv1d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size):
        super(CausalConv1d, self).__init__()
        self.conv1d = nn.Conv1d(in_channels, out_channels, kernel_size, padding=0, bias=False)
        self.kernel_size = kernel_size

    def forward(self, x):
        # Calculate the amount of padding needed
        padding_size = self.kernel_size - 1
        # Apply padding on the left side (i.e., before the start of the sequence)
        x_padded = nn.functional.pad(x, (padding_size, 0))
        # Apply the convolution
        return self.conv1d(x_padded)

class Transformer_Based_Model(nn.Module):
    def __init__(self, dataset, temp, D_text, D_visual, D_audio, n_head,
                 n_classes, hidden_dim, n_speakers, dropout):
        super(Transformer_Based_Model, self).__init__()
        self.temp = temp
        self.n_classes = n_classes
        self.n_speakers = n_speakers
        if self.n_speakers == 2:
            padding_idx = 2
        if self.n_speakers == 9:
            padding_idx = 9
        self.speaker_embeddings = nn.Embedding(n_speakers+1, hidden_dim, padding_idx)
        
        # Temporal convolutional layers(original)
        self.textf_input = nn.Conv1d(D_text, hidden_dim, kernel_size=1, padding=0, bias=False)
        self.acouf_input= nn.Conv1d(D_audio, hidden_dim, kernel_size=1, padding=0, bias=False)
        self.visuf_input = nn.Conv1d(D_visual, hidden_dim, kernel_size=1, padding=0, bias=False)
        
        # Intra- and Inter-modal Transformers
        self.t_t = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.a_t = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.v_t = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)

        self.a_a = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.t_a = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.v_a = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)

        self.v_v = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.t_v = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.a_v = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        
        # Unimodal-level Gated Fusion
        self.t_t_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.a_t_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.v_t_gate = Unimodal_GatedFusion(hidden_dim, dataset)

        self.a_a_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.t_a_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.v_a_gate = Unimodal_GatedFusion(hidden_dim, dataset)

        self.v_v_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.t_v_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.a_v_gate = Unimodal_GatedFusion(hidden_dim, dataset)

        # One modality only
        self.features_reduce_t = nn.Linear(hidden_dim, hidden_dim)  
        self.features_reduce_a = nn.Linear(hidden_dim, hidden_dim)
        self.features_reduce_v = nn.Linear(hidden_dim, hidden_dim)

        # For 'at' modality
        self.features_reduce_t_AT = nn.Linear(2*hidden_dim, hidden_dim)  
        self.features_reduce_a_AT = nn.Linear(2*hidden_dim, hidden_dim)   

        # One 'atv' modality
        self.features_reduce_t_ATV = nn.Linear(3*hidden_dim, hidden_dim)  
        self.features_reduce_a_ATV = nn.Linear(3*hidden_dim, hidden_dim)
        self.features_reduce_v_ATV = nn.Linear(3*hidden_dim, hidden_dim)

        # Multimodal-level Gated Fusion for single, double and triple modality
        self.last_gate_one= Multimodal_GatedFusion_one(hidden_dim)
        self.last_gate_two= Multimodal_GatedFusion_two(hidden_dim)
        self.last_gate_three= Multimodal_GatedFusion_three(hidden_dim)

        # Emotion Classifier
        self.t_output_layer = nn.Sequential(
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, n_classes)
            )
        self.a_output_layer = nn.Sequential(
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, n_classes)
            )
        self.v_output_layer = nn.Sequential(
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, n_classes)
            )
        self.all_output_layer = nn.Linear(hidden_dim, n_classes)

    def forward(self, textf, visuf, acouf, u_mask, qmask, dia_len, modality, setting):
        spk_idx = torch.argmax(qmask, -1)
        origin_spk_idx = spk_idx
        if self.n_speakers == 2:
            for i, x in enumerate(dia_len):
                spk_idx[i, x:] = (2*torch.ones(origin_spk_idx[i].size(0)-x)).int().cuda()
        if self.n_speakers == 9:
            for i, x in enumerate(dia_len):
                spk_idx[i, x:] = (9*torch.ones(origin_spk_idx[i].size(0)-x)).int().cuda()
        spk_embeddings = self.speaker_embeddings(spk_idx)

        # Temporal convolutional layers
        textf = self.textf_input(textf.permute(1, 2, 0)).transpose(1, 2)
        acouf = self.acouf_input(acouf.permute(1, 2, 0)).transpose(1, 2)
        visuf = self.visuf_input(visuf.permute(1, 2, 0)).transpose(1, 2)

        # Intra- and Inter-modal Transformers
        t_t_transformer_out = self.t_t(textf, textf, u_mask, spk_embeddings, setting)
        a_t_transformer_out = self.a_t(acouf, textf, u_mask, spk_embeddings, setting)
        v_t_transformer_out = self.v_t(visuf, textf, u_mask, spk_embeddings, setting)

        a_a_transformer_out = self.a_a(acouf, acouf, u_mask, spk_embeddings, setting)
        t_a_transformer_out = self.t_a(textf, acouf, u_mask, spk_embeddings, setting)
        v_a_transformer_out = self.v_a(visuf, acouf, u_mask, spk_embeddings, setting)

        v_v_transformer_out = self.v_v(visuf, visuf, u_mask, spk_embeddings, setting)
        t_v_transformer_out = self.t_v(textf, visuf, u_mask, spk_embeddings, setting)
        a_v_transformer_out = self.a_v(acouf, visuf, u_mask, spk_embeddings, setting)

        # Unimodal-level Gated Fusion
        t_t_transformer_out = self.t_t_gate(t_t_transformer_out)
        a_t_transformer_out = self.a_t_gate(a_t_transformer_out)
        v_t_transformer_out = self.v_t_gate(v_t_transformer_out)

        a_a_transformer_out = self.a_a_gate(a_a_transformer_out)
        t_a_transformer_out = self.t_a_gate(t_a_transformer_out)
        v_a_transformer_out = self.v_a_gate(v_a_transformer_out)

        v_v_transformer_out = self.v_v_gate(v_v_transformer_out)
        t_v_transformer_out = self.t_v_gate(t_v_transformer_out)
        a_v_transformer_out = self.a_v_gate(a_v_transformer_out)

        if modality=='t':
            t_transformer_out = self.features_reduce_t(t_t_transformer_out) 
            all_transformer_out = self.last_gate_one(t_transformer_out) 

            # Emotion Classifier
            t_final_out = self.t_output_layer(t_transformer_out)
            all_final_out = self.all_output_layer(all_transformer_out)

            t_log_prob = F.log_softmax(t_final_out, 2)
            all_log_prob = F.log_softmax(all_final_out, 2)
            all_prob = F.softmax(all_final_out, 2)
            kl_t_log_prob = F.log_softmax(t_final_out /self.temp, 2)
            kl_all_prob = F.softmax(all_final_out /self.temp, 2)

            return t_log_prob, all_log_prob, all_prob,\
                   kl_t_log_prob, kl_all_prob

        elif modality=='a':
            a_transformer_out = self.features_reduce_a(a_a_transformer_out) 
            all_transformer_out = self.last_gate_one(a_transformer_out) 

            # Emotion Classifier
            a_final_out = self.a_output_layer(a_transformer_out)
            all_final_out = self.all_output_layer(all_transformer_out)

            a_log_prob = F.log_softmax(a_final_out, 2)
            all_log_prob = F.log_softmax(all_final_out, 2)
            all_prob = F.softmax(all_final_out, 2)
            kl_a_log_prob = F.log_softmax(a_final_out /self.temp, 2)
            kl_all_prob = F.softmax(all_final_out /self.temp, 2)
            return a_log_prob, all_log_prob, all_prob,\
                   kl_a_log_prob, kl_all_prob

        elif modality=='at':
            t_transformer_out = self.features_reduce_t_AT(torch.cat([t_t_transformer_out, a_t_transformer_out], dim=-1))
            a_transformer_out = self.features_reduce_a_AT(torch.cat([a_a_transformer_out, t_a_transformer_out], dim=-1))
            all_transformer_out = self.last_gate_two(t_transformer_out, a_transformer_out) 

            # Emotion Classifier
            t_final_out = self.t_output_layer(t_transformer_out)
            a_final_out = self.a_output_layer(a_transformer_out) 
            all_final_out = self.all_output_layer(all_transformer_out)

            t_log_prob = F.log_softmax(t_final_out, 2)
            a_log_prob = F.log_softmax(a_final_out, 2)
            all_log_prob = F.log_softmax(all_final_out, 2)
            all_prob = F.softmax(all_final_out, 2)

            kl_t_log_prob = F.log_softmax(t_final_out /self.temp, 2)
            kl_a_log_prob = F.log_softmax(a_final_out /self.temp, 2)
            kl_all_prob = F.softmax(all_final_out /self.temp, 2)

            return t_log_prob, a_log_prob, all_log_prob, all_prob, \
               kl_t_log_prob, kl_a_log_prob , kl_all_prob

        else:
            t_transformer_out = self.features_reduce_t_ATV(torch.cat([t_t_transformer_out, a_t_transformer_out, v_t_transformer_out], dim=-1))
            a_transformer_out = self.features_reduce_a_ATV(torch.cat([a_a_transformer_out, t_a_transformer_out, v_a_transformer_out], dim=-1))
            v_transformer_out = self.features_reduce_v_ATV(torch.cat([v_v_transformer_out, t_v_transformer_out, a_v_transformer_out], dim=-1))
            all_transformer_out = self.last_gate_three(t_transformer_out, a_transformer_out, v_transformer_out) 

            # Emotion Classifier
            t_final_out = self.t_output_layer(t_transformer_out)
            a_final_out = self.a_output_layer(a_transformer_out)
            v_final_out = self.v_output_layer(v_transformer_out)
            all_final_out = self.all_output_layer(all_transformer_out)

            t_log_prob = F.log_softmax(t_final_out, 2)
            a_log_prob = F.log_softmax(a_final_out, 2)
            v_log_prob = F.log_softmax(v_final_out, 2)

            all_log_prob = F.log_softmax(all_final_out, 2)
            all_prob = F.softmax(all_final_out, 2)

            kl_t_log_prob = F.log_softmax(t_final_out /self.temp, 2)
            kl_a_log_prob = F.log_softmax(a_final_out /self.temp, 2)
            kl_v_log_prob = F.log_softmax(v_final_out /self.temp, 2)

            kl_all_prob = F.softmax(all_final_out /self.temp, 2)

            return t_log_prob, a_log_prob, v_log_prob, all_log_prob, all_prob, \
               kl_t_log_prob, kl_a_log_prob, kl_v_log_prob, kl_all_prob

'''/Ritesh
This is the realtime model:
Changes:
1. used casual convolution
2. casual masking in TransformerEncoder
/'''
class Transformer_Based_Model_diverse(nn.Module):
    def __init__(self, dataset, temp, D_text, D_visual, D_audio, n_head,
                 n_classes, hidden_dim, n_speakers, dropout):
        super(Transformer_Based_Model_diverse, self).__init__()
        self.temp = temp
        self.n_classes = n_classes
        self.n_speakers = n_speakers
        if self.n_speakers == 2:
            padding_idx = 2
        if self.n_speakers == 9:
            padding_idx = 9
        self.speaker_embeddings = nn.Embedding(n_speakers+1, hidden_dim, padding_idx)
        
        # Temporal convolutional layers(realtime)
        self.textf_input = CausalConv1d(D_text, hidden_dim, kernel_size=1)
        self.acouf_input = CausalConv1d(D_audio, hidden_dim, kernel_size=1)
        self.visuf_input = CausalConv1d(D_visual, hidden_dim, kernel_size=1)

        # Intra- and Inter-modal Transformers
        self.t_t = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.a_t = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.v_t = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)

        self.a_a = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.t_a = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.v_a = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)

        self.v_v = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.t_v = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        self.a_v = TransformerEncoder(d_model=hidden_dim, d_ff=hidden_dim, heads=n_head, layers=1, dropout=dropout)
        
        # Unimodal-level Gated Fusion
        self.t_t_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.a_t_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.v_t_gate = Unimodal_GatedFusion(hidden_dim, dataset)

        self.a_a_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.t_a_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.v_a_gate = Unimodal_GatedFusion(hidden_dim, dataset)

        self.v_v_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.t_v_gate = Unimodal_GatedFusion(hidden_dim, dataset)
        self.a_v_gate = Unimodal_GatedFusion(hidden_dim, dataset)

        # One modality only
        self.features_reduce_t = nn.Linear(hidden_dim, hidden_dim)  
        self.features_reduce_a = nn.Linear(hidden_dim, hidden_dim)
        self.features_reduce_v = nn.Linear(hidden_dim, hidden_dim)

        # For 'at' modality
        self.features_reduce_t_AT = nn.Linear(2*hidden_dim, hidden_dim)  
        self.features_reduce_a_AT = nn.Linear(2*hidden_dim, hidden_dim)   

        # One 'atv' modality
        self.features_reduce_t_ATV = nn.Linear(3*hidden_dim, hidden_dim)  
        self.features_reduce_a_ATV = nn.Linear(3*hidden_dim, hidden_dim)
        self.features_reduce_v_ATV = nn.Linear(3*hidden_dim, hidden_dim)

        # Multimodal-level Gated Fusion for single, double and triple modality
        self.last_gate_one= Multimodal_GatedFusion_one(hidden_dim)
        self.last_gate_two= Multimodal_GatedFusion_two(hidden_dim)
        self.last_gate_three= Multimodal_GatedFusion_three(hidden_dim)

        # Emotion Classifier
        self.t_output_layer = nn.Sequential(
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, n_classes)
            )
        self.a_output_layer = nn.Sequential(
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, n_classes)
            )
        self.v_output_layer = nn.Sequential(
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, n_classes)
            )
        self.all_output_layer = nn.Linear(hidden_dim, n_classes)

    def forward(self, textf, visuf, acouf, u_mask, qmask, dia_len, modality, setting):
        spk_idx = torch.argmax(qmask, -1)
        origin_spk_idx = spk_idx
        if self.n_speakers == 2:
            for i, x in enumerate(dia_len):
                spk_idx[i, x:] = (2*torch.ones(origin_spk_idx[i].size(0)-x)).int().cuda()
        if self.n_speakers == 9:
            for i, x in enumerate(dia_len):
                spk_idx[i, x:] = (9*torch.ones(origin_spk_idx[i].size(0)-x)).int().cuda()
        spk_embeddings = self.speaker_embeddings(spk_idx)

        # Temporal convolutional layers
        textf = self.textf_input(textf.permute(1, 2, 0)).transpose(1, 2)
        acouf = self.acouf_input(acouf.permute(1, 2, 0)).transpose(1, 2)
        visuf = self.visuf_input(visuf.permute(1, 2, 0)).transpose(1, 2)

        # Intra- and Inter-modal Transformers
        t_t_transformer_out = self.t_t(textf, textf, u_mask, spk_embeddings, setting)
        a_t_transformer_out = self.a_t(acouf, textf, u_mask, spk_embeddings, setting)
        v_t_transformer_out = self.v_t(visuf, textf, u_mask, spk_embeddings, setting)

        a_a_transformer_out = self.a_a(acouf, acouf, u_mask, spk_embeddings, setting)
        t_a_transformer_out = self.t_a(textf, acouf, u_mask, spk_embeddings, setting)
        v_a_transformer_out = self.v_a(visuf, acouf, u_mask, spk_embeddings, setting)

        v_v_transformer_out = self.v_v(visuf, visuf, u_mask, spk_embeddings, setting)
        t_v_transformer_out = self.t_v(textf, visuf, u_mask, spk_embeddings, setting)
        a_v_transformer_out = self.a_v(acouf, visuf, u_mask, spk_embeddings, setting)

        # Unimodal-level Gated Fusion
        t_t_transformer_out = self.t_t_gate(t_t_transformer_out)
        a_t_transformer_out = self.a_t_gate(a_t_transformer_out)
        v_t_transformer_out = self.v_t_gate(v_t_transformer_out)

        a_a_transformer_out = self.a_a_gate(a_a_transformer_out)
        t_a_transformer_out = self.t_a_gate(t_a_transformer_out)
        v_a_transformer_out = self.v_a_gate(v_a_transformer_out)

        v_v_transformer_out = self.v_v_gate(v_v_transformer_out)
        t_v_transformer_out = self.t_v_gate(t_v_transformer_out)
        a_v_transformer_out = self.a_v_gate(a_v_transformer_out)

        if modality=='t':
            t_transformer_out = self.features_reduce_t(t_t_transformer_out) 
            all_transformer_out = self.last_gate_one(t_transformer_out) 

            # Emotion Classifier
            t_final_out = self.t_output_layer(t_transformer_out)
            all_final_out = self.all_output_layer(all_transformer_out)

            t_log_prob = F.log_softmax(t_final_out, 2)
            all_log_prob = F.log_softmax(all_final_out, 2)
            all_prob = F.softmax(all_final_out, 2)
            kl_t_log_prob = F.log_softmax(t_final_out /self.temp, 2)
            kl_all_prob = F.softmax(all_final_out /self.temp, 2)

            return t_log_prob, all_log_prob, all_prob,\
                   kl_t_log_prob, kl_all_prob

        elif modality=='a':
            a_transformer_out = self.features_reduce_a(a_a_transformer_out) 
            all_transformer_out = self.last_gate_one(a_transformer_out) 

            # Emotion Classifier
            a_final_out = self.a_output_layer(a_transformer_out)
            all_final_out = self.all_output_layer(all_transformer_out)

            a_log_prob = F.log_softmax(a_final_out, 2)
            all_log_prob = F.log_softmax(all_final_out, 2)
            all_prob = F.softmax(all_final_out, 2)
            kl_a_log_prob = F.log_softmax(a_final_out /self.temp, 2)
            kl_all_prob = F.softmax(all_final_out /self.temp, 2)
            return a_log_prob, all_log_prob, all_prob,\
                   kl_a_log_prob, kl_all_prob

        elif modality=='at':
            t_transformer_out = self.features_reduce_t_AT(torch.cat([t_t_transformer_out, a_t_transformer_out], dim=-1))
            a_transformer_out = self.features_reduce_a_AT(torch.cat([a_a_transformer_out, t_a_transformer_out], dim=-1))
            all_transformer_out = self.last_gate_two(t_transformer_out, a_transformer_out) 

            # Emotion Classifier
            t_final_out = self.t_output_layer(t_transformer_out)
            a_final_out = self.a_output_layer(a_transformer_out) 
            all_final_out = self.all_output_layer(all_transformer_out)

            t_log_prob = F.log_softmax(t_final_out, 2)
            a_log_prob = F.log_softmax(a_final_out, 2)
            all_log_prob = F.log_softmax(all_final_out, 2)
            all_prob = F.softmax(all_final_out, 2)

            kl_t_log_prob = F.log_softmax(t_final_out /self.temp, 2)
            kl_a_log_prob = F.log_softmax(a_final_out /self.temp, 2)
            kl_all_prob = F.softmax(all_final_out /self.temp, 2)

            return t_log_prob, a_log_prob, all_log_prob, all_prob, \
               kl_t_log_prob, kl_a_log_prob , kl_all_prob

        else:
            t_transformer_out = self.features_reduce_t_ATV(torch.cat([t_t_transformer_out, a_t_transformer_out, v_t_transformer_out], dim=-1))
            a_transformer_out = self.features_reduce_a_ATV(torch.cat([a_a_transformer_out, t_a_transformer_out, v_a_transformer_out], dim=-1))
            v_transformer_out = self.features_reduce_v_ATV(torch.cat([v_v_transformer_out, t_v_transformer_out, a_v_transformer_out], dim=-1))
            all_transformer_out = self.last_gate_three(t_transformer_out, a_transformer_out, v_transformer_out) 

            # Emotion Classifier
            t_final_out = self.t_output_layer(t_transformer_out)
            a_final_out = self.a_output_layer(a_transformer_out)
            v_final_out = self.v_output_layer(v_transformer_out)
            all_final_out = self.all_output_layer(all_transformer_out)

            t_log_prob = F.log_softmax(t_final_out, 2)
            a_log_prob = F.log_softmax(a_final_out, 2)
            v_log_prob = F.log_softmax(v_final_out, 2)

            all_log_prob = F.log_softmax(all_final_out, 2)
            all_prob = F.softmax(all_final_out, 2)

            kl_t_log_prob = F.log_softmax(t_final_out /self.temp, 2)
            kl_a_log_prob = F.log_softmax(a_final_out /self.temp, 2)
            kl_v_log_prob = F.log_softmax(v_final_out /self.temp, 2)

            kl_all_prob = F.softmax(all_final_out /self.temp, 2)

            return t_log_prob, a_log_prob, v_log_prob, all_log_prob, all_prob, \
               kl_t_log_prob, kl_a_log_prob, kl_v_log_prob, kl_all_prob