"""Llama Prompt Guard 2 (86M) as the input filter of condition G (draft; not yet run by the author of this file).

Decision rule fixed for this study: a document is "detected" when the class with the highest score is not
the benign class (index 0). Check the printed id2label at installation and record it in the freeze record.
Inputs longer than the 512-token limit are reported, not silently truncated.
"""
import hashlib
from pathlib import Path


def load(model):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model)
    network = AutoModelForSequenceClassification.from_pretrained(model)
    network.eval()
    labels = {int(k): v for k, v in network.config.id2label.items()}
    files = {}
    if Path(model).is_dir():
        files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(model).iterdir()) if p.is_file()}

    def classify(text):
        encoded = tokenizer(text, return_tensors='pt', truncation=False)
        tokens = int(encoded['input_ids'].shape[1])
        if tokens > 512:
            return {'detected': False, 'error': 'over_512_tokens', 'tokens': tokens}
        with torch.no_grad():
            scores = torch.softmax(network(**encoded).logits, dim=-1)[0].tolist()
        top = max(range(len(scores)), key=scores.__getitem__)
        return {'detected': top != 0, 'top_class': top, 'top_label': labels.get(top), 'scores': [round(s, 6) for s in scores], 'tokens': tokens}

    return classify, {'model': str(model), 'id2label': labels, 'files_sha256': files, 'rule': 'detected when the top class is not index 0'}
