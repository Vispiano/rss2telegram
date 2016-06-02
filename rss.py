#!/usr/bin/env python3
#-*- coding: UTF 8 -*-
import sys
import json
import time
import base64
import urllib
import logging
import binascii
import telegram
import traceback
import feedparser
import configparser
from datetime import datetime
from sqlalchemy.orm import mapper
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Table, Column, Integer, String, ForeignKey, update, and_


Base = declarative_base()


class source(object):
	""" Класс для парсинга RSS-канала.
	Выделяет из общей информации только интереующие нас поля: Заголовок, ссылку, дату публикации.
	"""
	def __init__(self, link):
		self.link = link
		self.news = []
		self.refresh()

	def refresh(self):
		data = feedparser.parse(self.link)
		self.news = [News(binascii.b2a_base64(i['title'].encode()).decode(),\
			binascii.b2a_base64(i['link'].encode()).decode(),\
			int(time.mktime(i['published_parsed']))) for i in data['entries']]

	def __repr__(self):
		return "<RSS ('%s','%s')>" % (self.link, len(self.news))

class bitly:
	def __init__(self,access_token):
		self.access_token = access_token

	def short_link(self, long_link):
		url = 'https://api-ssl.bitly.com/v3/shorten?access_token=%s&longUrl=%s&format=json'\
		 % (self.access_token, long_link)
		try:
			return json.loads(urllib.request.urlopen(url).read().decode('utf8'))['data']['url']
		except:
			return long_link

class News(Base):	
	"""
	Класс, описывающий объект новости. Так же, осуществляется взаимодействие с БД.
	Описание полей таблицы ниже.
	"""
	__tablename__ = 'news'
	id = Column(Integer, primary_key=True) # Порядковый номер новости
	text = Column(String) # Текст (Заголовок), который будет отправлен в сообщении
	link  = Column(String) # Ссылка на статью на сайте. Так же отправляется в сообщении
	date = Column(Integer)
	# Дата появления новости на сайте. Носит Чисто информационный характер. UNIX_TIME.
	publish = Column(Integer)
	# Планируемая дата публикации. Сообщение будет отправлено НЕ РАНЬШЕ этой даты. UNIX_TIME.
	chat_id = Column(Integer) 
	# Информационный столбец. В данной версии функциональной нагрузки не несет.
	message_id = Column(Integer) 
	# Информационный столбец. В данной версии функциональной нагрузки не несет.

	def __init__(self, text, link, date, publish=0,chat_id=0,message_id=0):
		self.link = link
		self.text  = text
		self.date = date
		self.publish = publish
		self.chat_id = chat_id
		self.message_id = message_id

	def _keys(self):
		return (self.text, self.link)

	def __eq__(self, other):
		return self._keys() == other._keys()

	def __hash__(self):
		return hash(self._keys())

	def __repr__(self):
		return "<News ('%s','%s', %s)>" % (base64.b64decode(self.text).decode(),\
			base64.b64decode(self.link).decode(),\
			datetime.fromtimestamp(self.publish))
			# Для зрительного восприятия данные декодируется

class database:
	"""
	Класс для обработки сессии SQLAlchemy.
	Так же включает в себя минимальный набор методов, вызываемых в управляющем классе.
	Названия методов говорящие.
	"""
	def __init__(self, obj):
		engine = create_engine(obj, echo=False)
		Session = sessionmaker(bind=engine)
		self.session = Session()
	
	def add_news(self, news):
		self.session.add(news)
		self.session.commit()

	def get_post_without_message_id(self):
		return self.session.query(News).filter(and_(News.message_id == 0,\
					News.publish<=int(time.mktime(time.localtime())))).all()

	def update(self, link, chat, msg_id):
		self.session.query(News).filter_by(link = link).update({"chat_id":chat, "message_id":msg_id})
		self.session.commit()

	def find_link(self,link):
		if self.session.query(News).filter_by(link = link).first(): return True
		else: return False 
	
class export_bot:
	def __init__(self):
		config = configparser.ConfigParser()
		config.read('./config')
		log_file = config['Export_params']['log_file']
		self.pub_pause = int(config['Export_params']['pub_pause'])
		self.delay_between_messages = int(config['Export_params']['delay_between_messages'])
		logging.basicConfig(format = u'%(filename)s[LINE:%(lineno)d]# %(levelname)-8s \
							[%(asctime)s] %(message)s',level = logging.INFO, filename = u'%s'%log_file)
		self.db = database(config['Database']['Path'])
		self.src = source(config['RSS']['link'])
		self.chat_id = config['Telegram']['chat']
		bot_access_token = config['Telegram']['access_token']
		self.bot = telegram.Bot(token=bot_access_token)
		self.bit_ly = bitly(config['Bitly']['access_token'])
	
	def detect(self):
		#получаем 30 последних постов из rss-канала
		self.src.refresh()
		news = self.src.news		
		news.reverse()
		#Проверяем на наличие в базе ссылки на новость, если нет, то добавляем в базу данных с 
		#отложенной публикацией
		for i in news:
			if not self.db.find_link(i.link):
				now = int(time.mktime(time.localtime()))
				i.publish = now + self.pub_pause
				logging.info( u'Detect news: %s' % i)
				self.db.add_news(i)

	def public_posts(self):
		#Получаем 30 последних записей из rss канала и новости из БД, у которых message_id=0
		posts_from_db = self.db.get_post_without_message_id()
		self.src.refresh()
		line = [i for i in self.src.news]
		#Выбор пересечний этих списков
		for_publishing = list(set(line) & set(posts_from_db))
		for_publishing.reverse()
		print(for_publishing)
		#Постинг каждого сообщений
		for post in for_publishing:
			text = '%s %s' % (base64.b64decode(post.text).decode('utf8'),\
							  self.bit_ly.short_link(base64.b64decode(post.link).decode('utf-8')))
			a = self.bot.sendMessage(chat_id=self.chat_id, text=text, parse_mode=telegram.ParseMode.HTML)
			message_id = a.message_id
			chat_id = a['chat']['id']
			self.db.update(post.link, chat_id, message_id)
			logging.info( u'Public: %s;%s;' % (post, message_id))
			time.sleep(self.delay_between_messages)
