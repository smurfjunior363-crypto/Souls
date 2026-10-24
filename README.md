# Soulslike Bot

Bot de Discord para a comunidade soulslike: a pessoa registra uma run (jogo, desafio/build, tempo e print),
a moderação aprova e o bot mantém rankings de tempo por jogo e desafio.

## Comandos
- `/soulslike registrar` – jogo e desafio em lista, tempo (`1:30:45`, `1h30m`...), build opcional e print; prévia com Confirmar/Cancelar
- `/soulslike ranking` – ranking por tempo, com menus para trocar jogo e desafio
- `/soulslike meus` – seus registros e o status de cada um
- `/soulslike cancelar` – cancela uma run pendente
- `/soulslike ajuda` – explica como funciona
- `/sl_admin jogo_adicionar | desafio_adicionar | invalidar` – só para quem tem Gerenciar servidor

## Regras
- O ranking usa o melhor tempo de cada jogador; toda zerada fica salva (🔁).
- Moderador não aprova a própria run (exceto quem tem Administrador).

## Rodar
```bash
pip install -r requirements.txt
cp .env.example .env   # preencha
python bot.py
```
