"""Camada de acesso a dados.

Toda consulta e parametrizada, sem excecao. Nenhuma montagem de SQL por
concatenacao de texto, f-string ou operador de formatacao.

O hospede_repo cifra na escrita e decifra na leitura, de modo que o
restante da aplicacao nunca manipule ciphertext.
"""
