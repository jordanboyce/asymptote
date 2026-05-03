"""Chat orchestration package.

Houses the engines that drive an LLM conversation (agentic tool-use loop or
single completion) and the chat-specific context builder that prepares their
inputs from a request. Endpoints stay thin shells over these modules.
"""
