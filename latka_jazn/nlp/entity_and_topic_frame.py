from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any
SCHEMA_VERSION="entity_and_topic_frame/v14.6.10"
@dataclass(slots=True)
class EntityTopicFrame:
    entities: list[dict[str,Any]]=field(default_factory=list); topics: list[str]=field(default_factory=list); schema_version: str=SCHEMA_VERSION
    def to_dict(self)->dict[str,Any]: return asdict(self)
class EntityAndTopicFrameBuilder:
    def build(self,text:str)->EntityTopicFrame:
        low=(text or '').lower(); topics=[]
        for key,topic in [('runtime','runtime'),('jaźń','jazn'),('jazn','jazn'),('tekst','creative_text'),('zip','package'),('słownik','dictionary'),('slownik','dictionary')]:
            if key in low and topic not in topics: topics.append(topic)
        return EntityTopicFrame([], topics)
