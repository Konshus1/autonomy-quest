from copy import deepcopy

def base_renderer(message):
    return {"text": message["body"], "metadata": []}

def subject(template="Subject: {subject}"): return ("subject", template)
def redact(fields, replacement="[REDACTED]"): return ("redact", tuple(fields), replacement)
def legal(text): return ("legal", text)
def record(label): return ("record", label)

def make_renderer(renderer, capabilities):
    unique=[]
    for cap in capabilities:
        if not isinstance(cap, tuple) or not cap or cap[0] not in {"subject","redact","legal","record"}: raise TypeError("bad capability")
        if cap not in unique: unique.append(cap)
    def render(message):
        result=deepcopy(renderer(deepcopy(message)))
        for cap in unique:
            if cap[0]=="subject": result["text"]=cap[1].format(**message)+"\n"+result["text"]
            elif cap[0]=="redact":
                for field in cap[1]:
                    value=message.get(field)
                    if isinstance(value,str): result["text"]=result["text"].replace(value,cap[2])
            elif cap[0]=="legal": result["text"] += "\n"+cap[1]
            else: result["metadata"].append(cap[1])
        return result
    return render
