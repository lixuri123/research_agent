"""Memory package. Legacy backends are loaded only when explicitly requested."""
from research_agent.memory.base import ContextPack, MemoryManager, MemoryStore, get_memory_manager
from research_agent.memory.light import LightMemoryStore

__all__ = ['ContextPack', 'MemoryManager', 'MemoryStore', 'LightMemoryStore', 'get_memory_manager',
           'ContextAssembler', 'MemoryCompressor', 'ShortTermMemory', 'LongTermMemory',
           'EntityMemory', 'Entity', 'EpisodicMemory', 'Episode']


def __getattr__(name):
    legacy = {
        'ContextAssembler': ('assembler', 'ContextAssembler'),
        'MemoryCompressor': ('compressor', 'MemoryCompressor'),
        'ShortTermMemory': ('short_term', 'ShortTermMemory'),
        'LongTermMemory': ('long_term', 'LongTermMemory'),
        'EntityMemory': ('entity', 'EntityMemory'),
        'Entity': ('entity', 'Entity'),
        'EpisodicMemory': ('episodic', 'EpisodicMemory'),
        'Episode': ('episodic', 'Episode'),
    }
    if name not in legacy:
        raise AttributeError(name)
    from importlib import import_module
    module, attr = legacy[name]
    return getattr(import_module(f'research_agent.memory.{module}'), attr)
