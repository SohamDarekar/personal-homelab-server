"""What a module is handed when it starts: the shared pieces, and nothing else."""


class App:
    def __init__(self, config, router, scheduler, caps, store, llm=None, telegram=None, intents=None, text_hooks=None):
        self.config, self.router, self.scheduler, self.caps = config, router, scheduler, caps
        self.store, self.llm, self.telegram = store, llm, telegram
        self.intents = intents if intents is not None else {}  # name -> function(text) -> reply, for the bot's router
        self.text_hooks = text_hooks if text_hooks is not None else []  # function(text, lowered) -> True when it answered; tried before the classifier
