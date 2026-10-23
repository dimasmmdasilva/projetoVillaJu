"""Camada de acesso a dados.

Toda consulta é parametrizada, sem exceção. Nenhuma montagem de SQL por
concatenação de texto, f-string ou operador de formatação.

O hospede_repo cifra na escrita e decifra na leitura, de modo que o
restante da aplicação nunca manipule ciphertext.
"""
