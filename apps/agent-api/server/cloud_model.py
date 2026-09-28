"""Memoize model responses in the task journal, including tool call IDs."""
import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import messages_to_dict, messages_from_dict
from langchain_core.outputs import ChatGeneration, ChatResult
from .cloud_activity import activity_call, task_context

class DurableChatOpenAI(ChatOpenAI):
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        if not task_context.get(): return super()._generate(messages,stop,run_manager,**kwargs)
        def invoke(*_):
            result=super(DurableChatOpenAI,self)._generate(messages,stop,run_manager,**kwargs)
            return {'messages':messages_to_dict([g.message for g in result.generations]),
                    'generation_info':[g.generation_info for g in result.generations],
                    'llm_output':result.llm_output}
        identity=messages_to_dict(messages)
        for item in identity: item['data'].pop('id',None)
        result=activity_call('agent_model',invoke,self.model_name,identity,stop,kwargs)
        return ChatResult(generations=[ChatGeneration(message=m,generation_info=info)
            for m,info in zip(messages_from_dict(result['messages']),result['generation_info'])],
            llm_output=result['llm_output'])
