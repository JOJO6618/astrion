# server/chat/__init__.py - chat_bp 兼容入口
from flask import Blueprint

chat_bp = Blueprint("chat", __name__)

from server.chat.settings import *
from server.chat.files import *
from server.chat.permission import *
from server.chat.approval import *
from server.chat.terminal import *
from server.chat.misc import *
