"""Speech recognition (faster-whisper, CPU) in a disposable child process.

``runner`` is the child (``python -m vfe_vision.adapters.asr.runner``); ``client`` starts it,
keeps it in a Job Object and turns its JSON-lines events into an ``AsrResult``.
"""
