"""Rotas da aplicação, separadas por face.

A face interna exige sessão autenticada e verificação de perfil no
servidor, rota a rota. A face pública não compartilha sessão com ela e
acessa apenas chalé (leitura) e contato (escrita).
"""
