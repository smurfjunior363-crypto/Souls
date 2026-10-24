import io
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import get_text_channel

log = logging.getLogger("soulslike")

JOGOS_PADRAO = ["Elden Ring", "Dark Souls", "Dark Souls II", "Dark Souls III", "Bloodborne", "Sekiro", "Lies of P"]
DESAFIOS_PADRAO = [
    ("Any%", "Zerar o jogo o mais rápido possível, sem restrições extras."),
    ("Nível 1", "Zerar o jogo sem subir de nível."),
    ("Sem tomar dano", "Zerar o jogo sem levar nenhum golpe (no-hit)."),
    ("Todos os chefes", "Derrotar todos os chefes do jogo."),
]
MIN_SEGUNDOS = 30
MAX_SEGUNDOS = 1000 * 3600


def eh_moderador(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    return bool(config.MOD_ROLE_ID) and any(r.id == config.MOD_ROLE_ID for r in member.roles)


MEDALHAS = ["🥇", "🥈", "🥉"]


# ------------------------------------------------------------------ tempo
def parse_tempo(txt: str) -> int | None:
    """Aceita 1:30:45, 90:15, 1h30m45s, 1h30, 45m12s. Devolve segundos ou None."""
    t = txt.strip().lower().replace(" ", "")
    if not t:
        return None
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)(?:min|m))?(?:(\d+)s)?", t)
    if m and any(m.groups()):
        h, mi, s = (int(x or 0) for x in m.groups())
    elif (m := re.fullmatch(r"(\d+)h(\d{1,2})", t)):
        h, mi, s = int(m[1]), int(m[2]), 0
    elif re.fullmatch(r"\d+(:\d{1,2}){1,2}", t):
        p = [int(x) for x in t.split(":")]
        if len(p) == 2:
            h, mi, s = 0, p[0], p[1]
        else:
            h, mi, s = p
            if mi > 59:
                return None
        if s > 59:
            return None
    else:
        return None
    total = h * 3600 + mi * 60 + s
    return total if MIN_SEGUNDOS <= total <= MAX_SEGUNDOS else None


def fmt_tempo(seg: int) -> str:
    h, resto = divmod(seg, 3600)
    m, s = divmod(resto, 60)
    return f"{h}:{m:02d}:{s:02d}"


AJUDA_TEMPO = (
    "Não entendi o tempo. Use um destes formatos: `1:30:45`, `90:15` (minutos:segundos), "
    "`1h30m45s`, `1h30` ou `45m12s`. O mínimo é 30 segundos."
)

# ------------------------------------------------------------------ consultas
SQL_RANKING = """
SELECT r.id, r.user_id, r.seconds, r.build,
       (SELECT COUNT(*) FROM sl_runs r3 WHERE r3.user_id=r.user_id AND r3.game_id=r.game_id
          AND r3.challenge_id=r.challenge_id AND r3.status='approved') AS vezes
FROM sl_runs r
WHERE r.status='approved' AND r.game_id=? AND r.challenge_id=?
  AND r.id = (SELECT r2.id FROM sl_runs r2
              WHERE r2.user_id=r.user_id AND r2.game_id=r.game_id
                AND r2.challenge_id=r.challenge_id AND r2.status='approved'
              ORDER BY r2.seconds ASC, r2.id ASC LIMIT 1)
ORDER BY r.seconds ASC, r.id ASC LIMIT ?
"""

SQL_POSICAO = """
SELECT COUNT(DISTINCT user_id) AS c FROM sl_runs
WHERE status='approved' AND game_id=? AND challenge_id=? AND seconds < ? AND user_id != ?
"""

SQL_RUN_COMPLETA = """
SELECT r.*, g.name AS jogo, c.name AS desafio, c.rules AS regras
FROM sl_runs r JOIN sl_games g ON g.id=r.game_id JOIN sl_challenges c ON c.id=r.challenge_id
WHERE r.id=?
"""


class Soulslike(commands.Cog):
    soulslike = app_commands.Group(name="soulslike", description="Runs soulslike com ranking de tempo", guild_only=True)
    sl_admin = app_commands.Group(
        name="sl_admin",
        description="Administração do Soulslike",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        db = self.bot.db
        for nome in JOGOS_PADRAO:
            await db.conn.execute("INSERT OR IGNORE INTO sl_games(name) VALUES (?)", (nome,))
        for nome, regras in DESAFIOS_PADRAO:
            await db.conn.execute("INSERT OR IGNORE INTO sl_challenges(name, rules) VALUES (?,?)", (nome, regras))
        await db.conn.commit()
        self.bot.add_view(RevisaoRunView(self))

    # -------------------------------------------------------- auxiliares
    async def _jogo(self, nome: str):
        return await self.bot.db._one("SELECT * FROM sl_games WHERE name=?", (nome.strip(),))

    async def _desafio(self, nome: str):
        return await self.bot.db._one("SELECT * FROM sl_challenges WHERE name=?", (nome.strip(),))

    async def _posicao(self, game_id, chal_id, seg, user_id) -> int:
        row = await self.bot.db._one(SQL_POSICAO, (game_id, chal_id, seg, user_id))
        return row["c"] + 1

    async def ac_jogo(self, interaction: discord.Interaction, atual: str):
        rows = await self.bot.db._all("SELECT name FROM sl_games ORDER BY name")
        return [app_commands.Choice(name=r["name"], value=r["name"]) for r in rows if atual.lower() in r["name"].lower()][:25]

    async def ac_desafio(self, interaction: discord.Interaction, atual: str):
        rows = await self.bot.db._all("SELECT name FROM sl_challenges ORDER BY name")
        return [app_commands.Choice(name=r["name"], value=r["name"]) for r in rows if atual.lower() in r["name"].lower()][:25]

    async def embed_ranking(self, game_id: int, chal_id: int) -> discord.Embed:
        db = self.bot.db
        g = await db._one("SELECT * FROM sl_games WHERE id=?", (game_id,))
        c = await db._one("SELECT * FROM sl_challenges WHERE id=?", (chal_id,))
        rows = await db._all(SQL_RANKING, (game_id, chal_id, 10))
        e = discord.Embed(title=f"⚔️ {g['name']} — {c['name']}", color=config.COR_OURO)
        if c["rules"]:
            e.add_field(name="Regras do desafio", value=c["rules"], inline=False)
        if not rows:
            e.description = "Ninguém registrou essa categoria ainda. Seja o primeiro com `/soulslike registrar`!"
        else:
            linhas = []
            for i, r in enumerate(rows):
                pos = MEDALHAS[i] if i < 3 else f"`{i + 1}.`"
                build = f" • {r['build']}" if r["build"] and r["build"] != "Não informada" else ""
                vezes = f" • 🔁 {r['vezes']}x" if r["vezes"] > 1 else ""
                linhas.append(f"{pos} <@{r['user_id']}> — **{fmt_tempo(r['seconds'])}**{build}{vezes}")
            e.description = "\n".join(linhas)
        e.set_footer(text="O ranking usa o melhor tempo de cada jogador. 🔁 mostra quantas vezes ele zerou.")
        return e

    # -------------------------------------------------------- /soulslike registrar
    @soulslike.command(name="registrar", description="Registre uma run soulslike para entrar no ranking")
    @app_commands.describe(
        jogo="Escolha o jogo na lista",
        desafio="Escolha o desafio na lista",
        tempo="Tempo da run. Ex.: 1:30:45 ou 1h30m",
        captura="Print como comprovante",
        build="Sua build, classe ou arma (opcional)",
    )
    async def registrar(
        self,
        interaction: discord.Interaction,
        jogo: str,
        desafio: str,
        tempo: str,
        captura: discord.Attachment,
        build: str = "Não informada",
    ):
        g, c = await self._jogo(jogo), await self._desafio(desafio)
        if not g or not c:
            return await interaction.response.send_message(
                "Escolha o jogo e o desafio **na lista que aparece enquanto você digita**. "
                "Se faltar algum, peça a um moderador para cadastrar.",
                ephemeral=True,
            )
        seg = parse_tempo(tempo)
        if seg is None:
            return await interaction.response.send_message(AJUDA_TEMPO, ephemeral=True)
        if not (captura.content_type or "").startswith("image/"):
            return await interaction.response.send_message("O comprovante precisa ser uma imagem.", ephemeral=True)

        db = self.bot.db
        repetida = await db._one(
            """SELECT id FROM sl_runs WHERE user_id=? AND game_id=? AND challenge_id=?
               AND seconds=? AND status='pending'""",
            (interaction.user.id, g["id"], c["id"], seg),
        )
        if repetida:
            return await interaction.response.send_message(
                f"Você já enviou uma run igual (`#{repetida['id']}`) e ela está aguardando a moderação.",
                ephemeral=True,
            )
        melhor = await db._one(
            """SELECT MIN(seconds) AS m FROM sl_runs
               WHERE user_id=? AND game_id=? AND challenge_id=? AND status='approved'""",
            (interaction.user.id, g["id"], c["id"]),
        )

        dados = await captura.read()
        pos = await self._posicao(g["id"], c["id"], seg, interaction.user.id)
        prev = discord.Embed(title="Confira antes de enviar", color=config.COR_PRATA)
        prev.add_field(name="Jogo", value=g["name"])
        prev.add_field(name="Desafio", value=c["name"])
        prev.add_field(name="Tempo", value=fmt_tempo(seg))
        prev.add_field(name="Build", value=build[:100], inline=False)
        if c["rules"]:
            prev.add_field(name="Regras do desafio", value=c["rules"], inline=False)
        if melhor and melhor["m"] is not None and seg >= melhor["m"]:
            prev.set_footer(
                text=f"Seu melhor é {fmt_tempo(melhor['m'])}. Esse tempo não muda sua posição, "
                     "mas fica salvo como mais uma zerada."
            )
        else:
            prev.set_footer(text=f"Se for aprovado, você ficaria em {pos}º lugar.")
        view = ConfirmarView(self, interaction.user, g, c, seg, build[:100], dados, captura.filename)
        await interaction.response.send_message(embed=prev, view=view, ephemeral=True)

    async def enviar_para_moderacao(self, user, g, c, seg, build, dados, filename):
        db = self.bot.db
        canal = await get_text_channel(self.bot, config.MOD_CHANNEL_ID)
        if canal is None:
            return None
        cur = await db.conn.execute(
            "INSERT INTO sl_runs(user_id, game_id, challenge_id, build, seconds) VALUES (?,?,?,?,?)",
            (user.id, g["id"], c["id"], build, seg),
        )
        await db.conn.commit()
        run_id = cur.lastrowid

        record = await db._one(
            "SELECT MIN(seconds) AS m FROM sl_runs WHERE status='approved' AND game_id=? AND challenge_id=?",
            (g["id"], c["id"]),
        )
        pb = await db._one(
            "SELECT MIN(seconds) AS m FROM sl_runs WHERE status='approved' AND user_id=? AND game_id=? AND challenge_id=?",
            (user.id, g["id"], c["id"]),
        )
        pos = await self._posicao(g["id"], c["id"], seg, user.id)
        if pb and pb["m"] is not None and seg >= pb["m"]:
            ref = f"não melhora o melhor tempo dele ({fmt_tempo(pb['m'])}); conta como **mais uma zerada**"
        elif record and record["m"] is not None:
            dif = seg - record["m"]
            ref = "seria um **novo recorde**" if dif < 0 else f"seria o {pos}º, {fmt_tempo(dif)} mais lento que o recorde"
        else:
            ref = "seria o **primeiro registro** da categoria"

        embed = discord.Embed(title="⚔️ Run Soulslike aguardando aprovação", color=config.COR_PRATA)
        embed.add_field(name="Membro", value=user.mention)
        embed.add_field(name="Jogo", value=g["name"])
        embed.add_field(name="Desafio", value=c["name"])
        embed.add_field(name="Tempo", value=fmt_tempo(seg))
        embed.add_field(name="Build", value=build or "Não informada")
        embed.add_field(name="Posição", value=ref)
        embed.set_image(url=f"attachment://{filename}")
        embed.set_footer(text=f"SL:{run_id}")
        try:
            msg = await canal.send(
                embed=embed,
                file=discord.File(io.BytesIO(dados), filename=filename),
                view=RevisaoRunView(self),
            )
        except discord.HTTPException:
            await db.conn.execute("DELETE FROM sl_runs WHERE id=?", (run_id,))
            await db.conn.commit()
            return None
        url = msg.embeds[0].image.url if msg.embeds and msg.embeds[0].image else ""
        await db.conn.execute("UPDATE sl_runs SET proof_msg_id=?, proof_url=? WHERE id=?", (msg.id, url, run_id))
        await db.conn.commit()
        await db.log(user.id, "run_registrada", f"id={run_id} {g['name']}/{c['name']} {fmt_tempo(seg)}")
        return run_id

    # -------------------------------------------------------- /soulslike ranking
    @soulslike.command(name="ranking", description="Veja o ranking de tempo por jogo e desafio")
    @app_commands.describe(jogo="Jogo (opcional)", desafio="Desafio (opcional)")
    async def ranking(self, interaction: discord.Interaction, jogo: str | None = None, desafio: str | None = None):
        db = self.bot.db
        jogos = await db._all("SELECT * FROM sl_games ORDER BY name")
        desafios = await db._all("SELECT * FROM sl_challenges ORDER BY name")
        if not jogos or not desafios:
            return await interaction.response.send_message("Ainda não há jogos ou desafios cadastrados.", ephemeral=True)
        g = await self._jogo(jogo) if jogo else jogos[0]
        c = await self._desafio(desafio) if desafio else desafios[0]
        if not g or not c:
            return await interaction.response.send_message("Escolha o jogo e o desafio na lista.", ephemeral=True)
        view = RankingView(self, jogos, desafios, g["id"], c["id"])
        await interaction.response.send_message(embed=await self.embed_ranking(g["id"], c["id"]), view=view)

    # -------------------------------------------------------- /soulslike meus
    @soulslike.command(name="meus", description="Veja seus registros e o status de cada um")
    async def meus(self, interaction: discord.Interaction):
        rows = await self.bot.db._all(
            """SELECT r.*, g.name AS jogo, c.name AS desafio FROM sl_runs r
               JOIN sl_games g ON g.id=r.game_id JOIN sl_challenges c ON c.id=r.challenge_id
               WHERE r.user_id=? ORDER BY r.id DESC LIMIT 15""",
            (interaction.user.id,),
        )
        if not rows:
            return await interaction.response.send_message("Você ainda não registrou nenhuma run.", ephemeral=True)
        icones = {"pending": "⏳ Pendente", "approved": "✅ Aprovada", "rejected": "❌ Rejeitada",
                  "cancelled": "🚫 Cancelada", "invalidated": "⚠️ Invalidada"}
        linhas = []
        for r in rows:
            extra = f"\n   ↳ Motivo: {r['reject_reason']}" if r["status"] == "rejected" and r["reject_reason"] else ""
            linhas.append(f"`#{r['id']}` **{r['jogo']}** — {r['desafio']} — {fmt_tempo(r['seconds'])} • {icones.get(r['status'], r['status'])}{extra}")
        e = discord.Embed(title="Seus registros Soulslike", description="\n".join(linhas), color=config.COR_PRATA)
        e.set_footer(text="Para cancelar uma run pendente: /soulslike cancelar")
        await interaction.response.send_message(embed=e, ephemeral=True)

    # -------------------------------------------------------- /soulslike cancelar
    @soulslike.command(name="cancelar", description="Cancela uma run sua que ainda está pendente")
    @app_commands.describe(numero="O número do registro (aparece em /soulslike meus)")
    async def cancelar(self, interaction: discord.Interaction, numero: int):
        db = self.bot.db
        run = await db._one("SELECT * FROM sl_runs WHERE id=? AND user_id=?", (numero, interaction.user.id))
        if run is None or run["status"] != "pending":
            return await interaction.response.send_message("Não achei uma run pendente sua com esse número.", ephemeral=True)
        await db.conn.execute("UPDATE sl_runs SET status='cancelled' WHERE id=?", (numero,))
        await db.conn.commit()
        canal = await get_text_channel(self.bot, config.MOD_CHANNEL_ID)
        if canal and run["proof_msg_id"]:
            try:
                msg = await canal.fetch_message(run["proof_msg_id"])
                emb = msg.embeds[0]
                emb.color = discord.Color.dark_grey()
                emb.add_field(name="Resultado", value="🚫 Cancelada pelo autor", inline=False)
                await msg.edit(embed=emb, view=None)
            except discord.HTTPException:
                pass
        await interaction.response.send_message(f"Run `#{numero}` cancelada.", ephemeral=True)

    # -------------------------------------------------------- /soulslike ajuda
    @soulslike.command(name="ajuda", description="Como registrar uma run e entrar no ranking")
    async def ajuda(self, interaction: discord.Interaction):
        e = discord.Embed(title="⚔️ Como funciona", color=config.COR_OURO)
        e.description = (
            "1. Use `/soulslike registrar`, escolha o **jogo** e o **desafio** na lista, informe o **tempo** "
            "(ex.: `1:30:45` ou `1h30m`), a **build** (opcional) e anexe um **print**.\n"
            "2. Confira a prévia e confirme. A moderação aprova ou rejeita, e você recebe o motivo se for rejeitado.\n"
            "3. Veja o ranking com `/soulslike ranking` e seus registros com `/soulslike meus`.\n\n"
            "O ranking usa o **melhor tempo** de cada jogador, mas toda vez que você zera fica salvo (🔁)."
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    # -------------------------------------------------------- admin
    @sl_admin.command(name="jogo_adicionar", description="Adiciona um jogo à lista")
    async def jogo_adicionar(self, interaction: discord.Interaction, nome: str):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("Apenas moderadores.", ephemeral=True)
        nome = nome.strip()[:80]
        cur = await self.bot.db.conn.execute("INSERT OR IGNORE INTO sl_games(name) VALUES (?)", (nome,))
        await self.bot.db.conn.commit()
        msg = f"Jogo **{nome}** adicionado." if cur.rowcount else f"**{nome}** já existe."
        await self.bot.db.log(interaction.user.id, "sl_jogo_adicionar", nome)
        await interaction.response.send_message(msg, ephemeral=True)

    @sl_admin.command(name="desafio_adicionar", description="Adiciona um desafio à lista")
    @app_commands.describe(nome="Nome do desafio", regras="Regras que aparecem para quem registra")
    async def desafio_adicionar(self, interaction: discord.Interaction, nome: str, regras: str = ""):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("Apenas moderadores.", ephemeral=True)
        nome = nome.strip()[:80]
        cur = await self.bot.db.conn.execute(
            "INSERT OR IGNORE INTO sl_challenges(name, rules) VALUES (?,?)", (nome, regras.strip()[:300])
        )
        await self.bot.db.conn.commit()
        msg = f"Desafio **{nome}** adicionado." if cur.rowcount else f"**{nome}** já existe."
        await self.bot.db.log(interaction.user.id, "sl_desafio_adicionar", nome)
        await interaction.response.send_message(msg, ephemeral=True)

    @sl_admin.command(name="invalidar", description="Invalida uma run já aprovada (tira do ranking)")
    @app_commands.describe(numero="Número da run", motivo="Motivo (fica no log)")
    async def invalidar(self, interaction: discord.Interaction, numero: int, motivo: str = ""):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("Apenas moderadores.", ephemeral=True)
        db = self.bot.db
        run = await db._one("SELECT * FROM sl_runs WHERE id=?", (numero,))
        if run is None or run["status"] != "approved":
            return await interaction.response.send_message("Só dá para invalidar uma run aprovada.", ephemeral=True)
        await db.conn.execute("UPDATE sl_runs SET status='invalidated', reject_reason=? WHERE id=?", (motivo[:300], numero))
        await db.conn.commit()
        await db.log(interaction.user.id, "run_invalidada", f"id={numero} motivo={motivo}")
        await interaction.response.send_message(f"Run `#{numero}` invalidada e fora do ranking.", ephemeral=True)

    # -------------------------------------------------------- decisões da moderação
    async def carregar_run(self, interaction: discord.Interaction):
        """Lê o número da run no rodapé do card. Já responde ao usuário se algo estiver errado."""
        try:
            run_id = int(interaction.message.embeds[0].footer.text.split(":")[1])
        except (IndexError, ValueError, AttributeError):
            await interaction.response.send_message("Não consegui identificar essa run.", ephemeral=True)
            return None
        run = await self.bot.db._one(SQL_RUN_COMPLETA, (run_id,))
        if run is None or run["status"] != "pending":
            await interaction.response.send_message("Este registro já foi processado.", ephemeral=True)
            return None
        return run

    async def aprovar(self, interaction: discord.Interaction):
        if not eh_moderador(interaction.user):
            return await interaction.response.send_message("Apenas moderadores.", ephemeral=True)
        run = await self.carregar_run(interaction)
        if run is None:
            return
        if run["user_id"] == interaction.user.id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Você não pode aprovar a sua própria run.", ephemeral=True)

        db = self.bot.db
        antes = await db._one(
            "SELECT MIN(seconds) AS m FROM sl_runs WHERE status='approved' AND game_id=? AND challenge_id=?",
            (run["game_id"], run["challenge_id"]),
        )
        pb_antes = await db._one(
            "SELECT MIN(seconds) AS m FROM sl_runs WHERE status='approved' AND user_id=? AND game_id=? AND challenge_id=?",
            (run["user_id"], run["game_id"], run["challenge_id"]),
        )
        ja_tinha = await db._one(
            "SELECT COUNT(*) AS c FROM sl_runs WHERE status='approved' AND user_id=? AND game_id=? AND challenge_id=?",
            (run["user_id"], run["game_id"], run["challenge_id"]),
        )
        await db.conn.execute("UPDATE sl_runs SET status='approved', reviewer_id=? WHERE id=?", (interaction.user.id, run["id"]))
        await db.conn.commit()
        await db.log(interaction.user.id, "run_aprovada", f"id={run['id']} user={run['user_id']}")

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Resultado", value=f"✅ Aprovada por {interaction.user.mention}", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)

        novo_recorde = antes is None or antes["m"] is None or run["seconds"] < antes["m"]
        melhorou = pb_antes is None or pb_antes["m"] is None or run["seconds"] < pb_antes["m"]
        tempo_no_ranking = run["seconds"] if melhorou else pb_antes["m"]
        pos = await self._posicao(run["game_id"], run["challenge_id"], tempo_no_ranking, run["user_id"])
        vezes = ja_tinha["c"] + 1

        canal = interaction.guild.get_channel(config.SOULSLIKE_CHANNEL_ID)
        if canal:
            if novo_recorde:
                titulo = "👑 NOVO RECORDE!"
            elif melhorou:
                titulo = "⚔️ NOVA RUN SOULSLIKE!"
            else:
                titulo = "🔁 ZEROU DE NOVO!"
            e = discord.Embed(
                title=titulo,
                description=f"<@{run['user_id']}> terminou **{run['jogo']}** no desafio **{run['desafio']}** em **{fmt_tempo(run['seconds'])}**!",
                color=config.COR_OURO,
            )
            e.add_field(name="Build", value=run["build"] or "Não informada")
            e.add_field(name="Posição no ranking", value=f"{pos}º" + ("" if melhorou else " (melhor tempo dele)"))
            if run["proof_url"]:
                e.set_image(url=run["proof_url"])
            if vezes > 1:
                e.add_field(name="Vezes que zerou", value=f"🔁 {vezes}x")
            await canal.send(embed=e)

    async def rejeitar(self, interaction: discord.Interaction):
        if not eh_moderador(interaction.user):
            return await interaction.response.send_message("Apenas moderadores.", ephemeral=True)
        run = await self.carregar_run(interaction)
        if run is None:
            return
        await interaction.response.send_modal(MotivoModal(self, run["id"]))


# ------------------------------------------------------------------ views
class ConfirmarView(discord.ui.View):
    def __init__(self, cog, user, g, c, seg, build, dados, filename):
        super().__init__(timeout=180)
        self.cog, self.user, self.g, self.c = cog, user, g, c
        self.seg, self.build, self.dados, self.filename = seg, build, dados, filename

    @discord.ui.button(label="Confirmar e enviar", style=discord.ButtonStyle.success, emoji="✅")
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="⏳ Enviando para a moderação...", embed=None, view=None)
        run_id = await self.cog.enviar_para_moderacao(self.user, self.g, self.c, self.seg, self.build, self.dados, self.filename)
        if run_id is None:
            return await interaction.edit_original_response(
                content="Não consegui enviar para a moderação. Avise um administrador (canal de aprovação não configurado ou sem permissão)."
            )
        await interaction.edit_original_response(
            content=f"✅ Run enviada para a moderação! Número `#{run_id}`. Acompanhe com `/soulslike meus`."
        )
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Registro cancelado. Nada foi enviado.", embed=None, view=None)
        self.stop()


class RevisaoRunView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.success, custom_id="sl:approve")
    async def aprovar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.aprovar(interaction)

    @discord.ui.button(label="Rejeitar", style=discord.ButtonStyle.danger, custom_id="sl:reject")
    async def rejeitar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.rejeitar(interaction)


class MotivoModal(discord.ui.Modal, title="Rejeitar run"):
    motivo = discord.ui.TextInput(
        label="Motivo (a pessoa vai receber isso)",
        style=discord.TextStyle.paragraph,
        max_length=300,
        placeholder="Ex.: o print não mostra o tempo de jogo.",
    )

    def __init__(self, cog, run_id: int):
        super().__init__()
        self.cog, self.run_id = cog, run_id

    async def on_submit(self, interaction: discord.Interaction):
        db = self.cog.bot.db
        run = await db._one("SELECT * FROM sl_runs WHERE id=?", (self.run_id,))
        if run is None or run["status"] != "pending":
            return await interaction.response.send_message("Este registro já foi processado.", ephemeral=True)
        await db.conn.execute(
            "UPDATE sl_runs SET status='rejected', reviewer_id=?, reject_reason=? WHERE id=?",
            (interaction.user.id, str(self.motivo), self.run_id),
        )
        await db.conn.commit()
        await db.log(interaction.user.id, "run_rejeitada", f"id={self.run_id} motivo={self.motivo}")
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Resultado", value=f"❌ Rejeitada por {interaction.user.mention}\nMotivo: {self.motivo}", inline=False)
        await interaction.response.edit_message(embed=embed, view=None)
        try:
            user = self.cog.bot.get_user(run["user_id"]) or await self.cog.bot.fetch_user(run["user_id"])
            await user.send(f"Sua run `#{self.run_id}` foi rejeitada.\nMotivo: {self.motivo}\nVocê pode registrar de novo com `/soulslike registrar`.")
        except discord.HTTPException:
            pass


class RankingView(discord.ui.View):
    def __init__(self, cog, jogos, desafios, game_id, chal_id):
        super().__init__(timeout=600)
        self.cog, self.game_id, self.chal_id = cog, game_id, chal_id
        self.sel_jogo = discord.ui.Select(
            placeholder="Jogo",
            options=[discord.SelectOption(label=j["name"], value=str(j["id"]), default=j["id"] == game_id) for j in jogos[:25]],
        )
        self.sel_desafio = discord.ui.Select(
            placeholder="Desafio",
            options=[discord.SelectOption(label=c["name"], value=str(c["id"]), default=c["id"] == chal_id) for c in desafios[:25]],
        )
        self.sel_jogo.callback = self.trocar_jogo
        self.sel_desafio.callback = self.trocar_desafio
        self.add_item(self.sel_jogo)
        self.add_item(self.sel_desafio)

    async def _atualizar(self, interaction: discord.Interaction):
        for o in self.sel_jogo.options:
            o.default = o.value == str(self.game_id)
        for o in self.sel_desafio.options:
            o.default = o.value == str(self.chal_id)
        await interaction.response.edit_message(embed=await self.cog.embed_ranking(self.game_id, self.chal_id), view=self)

    async def trocar_jogo(self, interaction: discord.Interaction):
        self.game_id = int(self.sel_jogo.values[0])
        await self._atualizar(interaction)

    async def trocar_desafio(self, interaction: discord.Interaction):
        self.chal_id = int(self.sel_desafio.values[0])
        await self._atualizar(interaction)


# autocompletar (mesma lista para registrar e ranking)
Soulslike.registrar.autocomplete("jogo")(Soulslike.ac_jogo)
Soulslike.registrar.autocomplete("desafio")(Soulslike.ac_desafio)
Soulslike.ranking.autocomplete("jogo")(Soulslike.ac_jogo)
Soulslike.ranking.autocomplete("desafio")(Soulslike.ac_desafio)


async def setup(bot):
    await bot.add_cog(Soulslike(bot))
