"""Rotas da aplicacao, separadas por face.

A face interna exige sessao autenticada e verificacao de perfil no
servidor, rota a rota. A face publica nao compartilha sessao com ela e
acessa apenas chale (leitura) e contato (escrita).
"""
