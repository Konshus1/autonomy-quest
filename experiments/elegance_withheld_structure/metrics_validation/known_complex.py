from abc import ABC, abstractmethod
from copy import deepcopy

class Capability(ABC):
    @abstractmethod
    def identity(self): raise NotImplementedError
    @abstractmethod
    def apply(self, message, result): raise NotImplementedError

class SubjectCapability(Capability):
    def __init__(self, template): self.template=template
    def identity(self): return ("subject",self.template)
    def apply(self,message,result): result["text"]=self.template.format(**message)+"\n"+result["text"]

class RedactCapability(Capability):
    def __init__(self,fields,replacement): self.fields=tuple(fields); self.replacement=replacement
    def identity(self): return ("redact",self.fields,self.replacement)
    def apply(self,message,result):
        for field in self.fields:
            if field in message:
                value=message[field]
                if isinstance(value,str): result["text"]=result["text"].replace(value,self.replacement)

class LegalCapability(Capability):
    def __init__(self,text): self.text=text
    def identity(self): return ("legal",self.text)
    def apply(self,message,result):
        if result["text"]: result["text"]=result["text"]+"\n"+self.text
        else: result["text"]=self.text

class RecordCapability(Capability):
    def __init__(self,label): self.label=label
    def identity(self): return ("record",self.label)
    def apply(self,message,result): result["metadata"].append(self.label)

def base_renderer(message):
    output={"text":"","metadata":[]}
    if "body" in message: output["text"]=message["body"]
    return output

def subject(template="Subject: {subject}"): return SubjectCapability(template)
def redact(fields,replacement="[REDACTED]"): return RedactCapability(fields,replacement)
def legal(text): return LegalCapability(text)
def record(label): return RecordCapability(label)

def make_renderer(renderer,capabilities):
    accepted=[]; seen=set()
    for capability in capabilities:
        if not isinstance(capability,Capability): raise TypeError("bad capability")
        key=capability.identity()
        if key in seen: continue
        seen.add(key); accepted.append(capability)
    class RenderingSession:
        def __init__(self,source): self.source=source
        def execute(self,message):
            working_message=deepcopy(message); original=renderer(working_message)
            if not isinstance(original,dict): raise TypeError("renderer result")
            if "text" not in original: raise ValueError("missing text")
            if "metadata" not in original: raise ValueError("missing metadata")
            if not isinstance(original["metadata"],list): raise TypeError("metadata")
            result={"text":str(original["text"]),"metadata":list(original["metadata"])}
            for capability in self.source: capability.apply(working_message,result)
            return result
    session=RenderingSession(accepted)
    def render(message): return session.execute(message)
    return render
