import json
import logging
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY nao esta definida no ambiente.")
    return anthropic.Anthropic(api_key=api_key)


def _parse_json_response(raw_text: str) -> dict:
    """Remove markdown code fences e faz parse do JSON."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    return json.loads(text)


def score_profile(username: str, profile_data: dict) -> dict:
    """Avalia o perfil do Instagram usando Claude e retorna scores estruturados.

    Args:
        username: Nome de usuario do perfil.
        profile_data: Dados coletados pelo instagram.get_profile().

    Returns:
        Dict com scores de bio, foto, destaques e totais.

    Raises:
        RuntimeError: Em caso de falha na API ou parse do JSON.
    """
    client = _get_client()

    biography = profile_data.get("biography", "")
    external_url = profile_data.get("external_url", "")
    highlights_count = profile_data.get("highlights_count", 0)
    profile_pic_url = profile_data.get("profile_pic_url", "")

    prompt = f"""Analise o perfil do Instagram abaixo e retorne uma avaliaÃ§Ã£o em JSON.

USERNAME: {username}
BIO: {biography}
LINK EXTERNO: {external_url}
FOTO DE PERFIL URL: {profile_pic_url}
DESTAQUES: {highlights_count} destaques encontrados

Avalie cada critÃ©rio com true (atende) ou false (nÃ£o atende):

BIO:
1. bio_identidade: A bio deixa claro quem Ã© a pessoa (profissÃ£o/nicho)?
2. bio_oferta: A bio menciona o que vende, ensina ou faz (proposta)?
3. bio_link: HÃ¡ um link externo preenchido (qualquer URL)?

FOTO (analise a URL da foto de perfil se possÃ­vel):
4. foto_rosto: Parece ser uma foto com rosto humano visÃ­vel?
5. foto_thumbnail: A descriÃ§Ã£o sugere que seria reconhecÃ­vel em tamanho pequeno?

DESTAQUES:
6. destaques_existem: HÃ¡ pelo menos 1 destaque? ({highlights_count} > 0)
7. destaques_organizados: Com {highlights_count} destaques, parece organizado?
8. destaques_negocio: Algum destaque provavelmente comunica o que vende ou quem Ã©?

Retorne APENAS um JSON vÃ¡lido neste formato:
{{
  "bio_identidade": true,
  "bio_oferta": true,
  "bio_link": false,
  "foto_rosto": true,
  "foto_thumbnail": true,
  "destaques_existem": true,
  "destaques_organizados": true,
  "destaques_negocio": true,
  "bio_score": 2,
  "foto_score": 2,
  "destaques_score": 3,
  "total_profile_score": 7
}}

Onde bio_score = soma dos 3 itens de bio (max 3), foto_score = soma dos 2 itens de foto (max 2), destaques_score = soma dos 3 itens de destaques (max 3), total_profile_score = soma de todos (max 8)."""

    raw_text = ""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
        result = _parse_json_response(raw_text)

        # Montar resposta estruturada compatÃ­vel com o frontend
        structured = {
            "bio": {
                "identidade": result.get("bio_identidade", False),
                "oferta": result.get("bio_oferta", False),
                "link": result.get("bio_link", False),
                "score": result.get("bio_score", 0),
            },
            "foto": {
                "rosto": result.get("foto_rosto", False),
                "thumbnail": result.get("foto_thumbnail", False),
                "score": result.get("foto_score", 0),
            },
            "destaques": {
                "existem": result.get("destaques_existem", False),
                "organizados": result.get("destaques_organizados", False),
                "negocio": result.get("destaques_negocio", False),
                "score": result.get("destaques_score", 0),
            },
            "total_profile_score": result.get("total_profile_score", 0),
            "max_profile_score": 8,
        }

        logger.info("Score de perfil calculado para @%s: %s/8", username, structured["total_profile_score"])
        return structured

    except json.JSONDecodeError as e:
        logger.error("Falha ao parsear JSON do score de perfil: %s\nResposta bruta: %s", e, raw_text)
        raise RuntimeError(f"Falha ao interpretar resposta do Claude para score de perfil: {str(e)}")

    except anthropic.APIError as e:
        logger.error("Erro na API Anthropic ao calcular score de perfil: %s", e)
        raise RuntimeError(f"Erro na API Claude: {str(e)}")


def score_video(transcricao: str, profile_score_data: dict) -> dict:
    """Avalia o roteiro do video usando a metodologia IDF real dos Comunicadores MagnÃ©ticos.

    ATENÃÃO: Hook NUNCA pode ser pergunta. Estrutura Ã© D1âD2(virada)âD3, nÃ£o formatos genÃ©ricos.

    Args:
        transcricao: Texto transcrito do audio do video.
        profile_score_data: Resultado do score_profile(), usado como contexto.

    Returns:
        Dict completo com scores de todas as dimensoes do video.

    Raises:
        RuntimeError: Em caso de falha na API ou parse do JSON.
    """
    client = _get_client()

    prompt = f"""VocÃª Ã© o DiagnÃ³stico â o agente analisador da metodologia Giullya Becker (Comunicadores MagnÃ©ticos).
Sua funÃ§Ã£o Ã© analisar a transcriÃ§Ã£o abaixo com olho clÃ­nico e retornar um diagnÃ³stico preciso.

TRANSCRIÃÃO DO REEL:
{transcricao}

---

METODOLOGIA DE ANÃLISE â 3 CAMADAS:

== CAMADA 1: GANCHO (primeiros ~15 segundos) ==

REGRAS ABSOLUTAS DO GANCHO:
- AfirmaÃ§Ã£o direta â NUNCA pergunta ao espectador
- ViÃ©s negativo â cria tensÃ£o, NUNCA promessa ("vou te mostrar", "hoje vocÃª vai aprender")
- NÃO revela a descoberta ou soluÃ§Ã£o antes do D1
- Linguagem comum â zero jargÃ£o tÃ©cnico ou nicho
- Deve criar incÃ´modo ou curiosidade nos primeiros 3 segundos
- Deve ter SUSPENSÃO antes de entrar no desenvolvimento ("Mas antes de te contar...", "SÃ³ que antes disso...")

VIOLAÃÃES CRÃTICAS DO GANCHO:
- Gancho com pergunta direta ao espectador
- Gancho com promessa de aprendizado
- Gancho que revela o insight ou soluÃ§Ã£o logo de cara
- AusÃªncia de suspensÃ£o antes do desenvolvimento
- Linguagem tÃ©cnica que exclui quem nÃ£o conhece o nicho

== CAMADA 2: ESTRUTURA IDF ==

InÃ­cio â Desenvolvimento (D1 â D2 â D3) â Fechamento

D1 (Contexto): Tem contexto concreto com detalhes reais? Estabelece a situaÃ§Ã£o sem revelar a virada?
D2 (Virada): Tem uma contradiÃ§Ã£o, virada ou insight REAL â ou Ã© apenas continuaÃ§Ã£o do D1?
D3 (Valor): Entrega valor sem virar aula? Para antes de explicar demais? Copy reduzida ao mÃ¡ximo?
Fechamento/CTA: Ã compatÃ­vel com o objetivo do vÃ­deo (AtraÃ§Ã£o / QualificaÃ§Ã£o / ConversÃ£o)?

REGRAS DO CTA POR OBJETIVO:
- AtraÃ§Ã£o: "Me segue" / "Segue pra ver mais sobre [tema]"
- QualificaÃ§Ã£o: "Comenta [X]" / "Salva esse vÃ­deo" / "Compartilha com quem precisa"
- ConversÃ£o: "Comenta [PALAVRA]" / "Link na bio" / "Me chama no direct"
- CTA de AtraÃ§Ã£o em vÃ­deo de ConversÃ£o = VIOLAÃÃO CRÃTICA

== CAMADA 3: CHECKLIST DE QUALIDADE (8 itens) ==
1. Prende atenÃ§Ã£o nos primeiros 3s
2. NÃ£o revela a descoberta no gancho
3. Linguagem comum, sem jargÃµes
4. Tem padrÃ£o de storytelling definido (IDF)
5. Varia tensÃ£o e alÃ­vio
6. Copy reduzida ao mÃ¡ximo
7. CTA especÃ­fico e direto
8. CTA alinhado com o objetivo do vÃ­deo

---

INSTRUÃÃES PARA O JSON DE RETORNO:

Para cada campo de observaÃ§Ã£o: SEMPRE cite o trecho real da transcriÃ§Ã£o analisado.
Severidade: "violacao" = quebra regra absoluta | "fraco" = risco mas nÃ£o viola | "ok" = funciona

Retorne APENAS um JSON vÃ¡lido neste formato:
{{
  "objetivo_identificado": "AtraÃ§Ã£o | QualificaÃ§Ã£o | ConversÃ£o",

  "gancho_score": 5,
  "gancho_trecho": "[primeiras palavras exatas do gancho conforme transcriÃ§Ã£o]",
  "gancho_tipo": "AfirmaÃ§Ã£o direta | Pergunta (VIOLAÃÃO) | Promessa (VIOLAÃÃO) | Pattern Interrupt | HistÃ³ria",
  "gancho_tem_suspensao": true,
  "gancho_trecho_suspensao": "[trecho da suspensÃ£o, se houver]",
  "gancho_violacoes": ["lista de violaÃ§Ãµes identificadas, vazia se nenhuma"],
  "gancho_severidade": "violacao | fraco | ok",
  "gancho_observacao": "DiagnÃ³stico com trecho citado. Ex: O gancho 'VocÃª jÃ¡ tentou...' Ã© uma pergunta direta â viola a regra de afirmaÃ§Ã£o.",

  "d1_score": 7,
  "d1_trecho": "[trecho representativo do D1]",
  "d1_tem_contexto_concreto": true,
  "d1_observacao": "DiagnÃ³stico com trecho citado.",

  "d2_score": 6,
  "d2_trecho": "[trecho representativo da virada]",
  "d2_tem_virada_real": true,
  "d2_observacao": "DiagnÃ³stico com trecho citado. Indicar se Ã© virada real ou extensÃ£o do D1.",

  "d3_score": 8,
  "d3_trecho": "[trecho representativo do D3]",
  "d3_virou_aula": false,
  "d3_observacao": "DiagnÃ³stico com trecho citado.",

  "cta_score": 4,
  "cta_trecho": "[trecho exato do CTA]",
  "cta_tipo_identificado": "AtraÃ§Ã£o | QualificaÃ§Ã£o | ConversÃ£o",
  "cta_alinhado_objetivo": true,
  "cta_observacao": "DiagnÃ³stico com trecho citado. Se desalinhado, explicar qual deveria ser.",

  "linguagem_score": 9,
  "linguagem_observacao": "DiagnÃ³stico com exemplos citados da transcriÃ§Ã£o.",

  "checklist": {{
    "prende_atencao_3s": true,
    "nao_revela_descoberta": true,
    "linguagem_comum": true,
    "tem_storytelling_idf": true,
    "varia_tensao_alivio": false,
    "copy_reduzida": true,
    "cta_especifico": true,
    "cta_alinhado": true,
    "total_ok": 7
  }},

  "pontos_fortes": "DiagnÃ³stico preciso com trechos citados do que estÃ¡ funcionando.",
  "principal_gap": "O gap mais crÃ­tico com trecho citado. Use linguagem de urgÃªncia: 'Se continuar assim, seu conteÃºdo vai continuar flopado porque...' ou 'Esse erro estÃ¡ sabotando ativamente seus resultados porque...'. Cite o trecho exato do erro.",
  "proximo_passo": "AÃ§Ã£o especÃ­fica e cirÃºrgica com urgÃªncia. Ex: Reescreva o gancho AGORA como afirmaÃ§Ã£o: em vez de 'VocÃª jÃ¡ tentou...' (que faz o espectador desengajar imediatamente), use 'Toda pessoa que tenta X sem Y estÃ¡ cometendo o erro que ninguÃ©m fala.'",

  "nivel_alerta": "critico | importante | atencao",
  "headline_diagnostico": "Frase curta e impactante com viÃ©s negativo para exibir no topo do relatÃ³rio. Exemplos: 'Encontramos 3 erros crÃ­ticos que estÃ£o sabotando seu perfil' | 'Seu gancho estÃ¡ expulsando seguidores antes de 3 segundos' | 'Identificamos gaps importantes que estÃ£o custando vendas' | 'AtenÃ§Ã£o: seu conteÃºdo tem potencial, mas esses ajustes sÃ£o urgentes'. Use sempre tom de alerta â nunca elogioso."
}}"""

    raw_text = ""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
        result = _parse_json_response(raw_text)

        logger.info(
            "Score de video calculado â gancho: %s, d1: %s, d2: %s, d3: %s, cta: %s, nivel: %s",
            result.get("gancho_score"),
            result.get("d1_score"),
            result.get("d2_score"),
            result.get("d3_score"),
            result.get("cta_score"),
            result.get("nivel_alerta"),
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("Falha ao parsear JSON do score de video: %s\nResposta bruta: %s", e, raw_text)
        raise RuntimeError(f"Falha ao interpretar resposta do Claude para score de video: {str(e)}")

    except anthropic.APIError as e:
        logger.error("Erro na API Anthropic ao calcular score de video: %s", e)
        raise RuntimeError(f"Erro na API Claude: {str(e)}")
