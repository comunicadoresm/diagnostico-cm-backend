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
    """Extrai e faz parse do JSON da resposta do Claude.
    Tolerante a code fences e texto extra antes/depois do JSON.
    """
    text = raw_text.strip()

    # Extrai conteúdo entre code fences se presentes
    if "```" in text:
        import re
        fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence_match:
            text = fence_match.group(1).strip()

    # Corta do primeiro { até o último } para eliminar texto extra
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last <= first:
        raise json.JSONDecodeError("Nenhum objeto JSON encontrado", text, 0)

    return json.loads(text[first : last + 1])


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

    prompt = f"""Analise o perfil do Instagram abaixo e retorne uma avaliação em JSON.

USERNAME: {username}
BIO: {biography}
LINK EXTERNO: {external_url}
FOTO DE PERFIL URL: {profile_pic_url}
DESTAQUES: {highlights_count} destaques encontrados

Avalie cada critério com true (atende) ou false (não atende):

BIO:
1. bio_identidade: A bio deixa claro quem é a pessoa (profissão/nicho)?
2. bio_oferta: A bio menciona o que vende, ensina ou faz (proposta)?
3. bio_link: Há um link externo preenchido (qualquer URL)?

FOTO (analise a URL da foto de perfil se possível):
4. foto_rosto: Parece ser uma foto com rosto humano visível?
5. foto_thumbnail: A descrição sugere que seria reconhecível em tamanho pequeno?

DESTAQUES:
6. destaques_existem: Há pelo menos 1 destaque? ({highlights_count} > 0)
7. destaques_organizados: Com {highlights_count} destaques, parece organizado?
8. destaques_negocio: Algum destaque provavelmente comunica o que vende ou quem é?

Retorne APENAS um JSON válido neste formato:
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

Onde bio_score = soma dos 3 itens de bio (max 3), foto_score = soma dos 2 itens de foto (max 2), destaques_score = soma dos 3 itens de destaques (max 3), total_profile_score = soma de todos (max 8).

Inclua também:
- "resumo_positivo": 1 frase direta sobre o maior ponto forte do perfil com base nos critérios acima. Ex: "Sua bio comunica claramente quem você é e o que vende — isso já filtra e atrai o público certo." Máximo 2 linhas, sem elogios genéricos.
- "resumo_melhoria": 1 frase direta sobre o ajuste mais urgente no perfil. Ex: "Você não tem link na bio — isso corta o caminho do seguidor até a sua oferta e precisa ser corrigido hoje." Máximo 2 linhas, tom de urgência, cite o elemento específico (bio, foto ou destaques)."""

    raw_text = ""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
        result = _parse_json_response(raw_text)

        # Montar resposta estruturada compatível com o frontend
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
            "resumo_positivo": result.get("resumo_positivo", ""),
            "resumo_melhoria": result.get("resumo_melhoria", ""),
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
    """Avalia o roteiro do video usando a metodologia IDF real dos Comunicadores Magnéticos.

    ATENÇÃO: Hook NUNCA pode ser pergunta. Estrutura é D1→D2(virada)→D3, não formatos genéricos.

    Args:
        transcricao: Texto transcrito do audio do video.
        profile_score_data: Resultado do score_profile(), usado como contexto.

    Returns:
        Dict completo com scores de todas as dimensoes do video.

    Raises:
        RuntimeError: Em caso de falha na API ou parse do JSON.
    """
    client = _get_client()

    prompt = f"""Você é o Diagnóstico — o agente analisador da metodologia Giullya Becker (Comunicadores Magnéticos).
Sua função é analisar a transcrição abaixo com olho clínico e retornar um diagnóstico preciso.

TRANSCRIÇÃO DO REEL:
{transcricao}

---

METODOLOGIA DE ANÁLISE — 3 CAMADAS:

== CAMADA 1: GANCHO (primeiros ~15 segundos) ==

REGRAS ABSOLUTAS DO GANCHO:
- Afirmação direta — NUNCA pergunta ao espectador
- Viés negativo — cria tensão, NUNCA promessa ("vou te mostrar", "hoje você vai aprender")
- NÃO revela a descoberta ou solução antes do D1
- Linguagem comum — zero jargão técnico ou nicho
- Deve criar incômodo ou curiosidade nos primeiros 3 segundos
- Deve ter SUSPENSÃO antes de entrar no desenvolvimento ("Mas antes de te contar...", "Só que antes disso...")

VIOLAÇÕES CRÍTICAS DO GANCHO:
- Gancho com pergunta direta ao espectador
- Gancho com promessa de aprendizado
- Gancho que revela o insight ou solução logo de cara
- Ausência de suspensão antes do desenvolvimento
- Linguagem técnica que exclui quem não conhece o nicho

== CAMADA 2: ESTRUTURA IDF ==

Início → Desenvolvimento (D1 → D2 → D3) → Fechamento

D1 (Contexto): Tem contexto concreto com detalhes reais? Estabelece a situação sem revelar a virada?
D2 (Virada): Tem uma contradição, virada ou insight REAL — ou é apenas continuação do D1?
D3 (Valor): Entrega valor sem virar aula? Para antes de explicar demais? Copy reduzida ao máximo?
Fechamento/CTA: É compatível com o objetivo do vídeo (Atração / Qualificação / Conversão)?

REGRAS DO CTA POR OBJETIVO:
- Atração: "Me segue" / "Segue pra ver mais sobre [tema]"
- Qualificação: "Comenta [X]" / "Salva esse vídeo" / "Compartilha com quem precisa"
- Conversão: "Comenta [PALAVRA]" / "Link na bio" / "Me chama no direct"
- CTA de Atração em vídeo de Conversão = VIOLAÇÃO CRÍTICA

== CAMADA 3: CHECKLIST DE QUALIDADE (8 itens) ==
1. Prende atenção nos primeiros 3s
2. Não revela a descoberta no gancho
3. Linguagem comum, sem jargões
4. Tem padrão de storytelling definido (IDF)
5. Varia tensão e alívio
6. Copy reduzida ao máximo
7. CTA específico e direto
8. CTA alinhado com o objetivo do vídeo

---

INSTRUÇÕES PARA O JSON DE RETORNO:

Para cada campo de observação: SEMPRE cite o trecho real da transcrição analisado.
Severidade: "violacao" = quebra regra absoluta | "fraco" = risco mas não viola | "ok" = funciona

Retorne APENAS um JSON válido neste formato:
{{
  "objetivo_identificado": "Atração | Qualificação | Conversão",

  "gancho_score": 5,
  "gancho_trecho": "[primeiras palavras exatas do gancho conforme transcrição]",
  "gancho_tipo": "Afirmação direta | Pergunta (VIOLAÇÃO) | Promessa (VIOLAÇÃO) | Pattern Interrupt | História",
  "gancho_tem_suspensao": true,
  "gancho_trecho_suspensao": "[trecho da suspensão, se houver]",
  "gancho_violacoes": ["lista de violações identificadas, vazia se nenhuma"],
  "gancho_severidade": "violacao | fraco | ok",
  "gancho_observacao": "Diagnóstico com trecho citado. Ex: O gancho 'Você já tentou...' é uma pergunta direta — viola a regra de afirmação.",

  "d1_score": 7,
  "d1_trecho": "[trecho representativo do D1]",
  "d1_tem_contexto_concreto": true,
  "d1_observacao": "Diagnóstico com trecho citado.",

  "d2_score": 6,
  "d2_trecho": "[trecho representativo da virada]",
  "d2_tem_virada_real": true,
  "d2_observacao": "Diagnóstico com trecho citado. Indicar se é virada real ou extensão do D1.",

  "d3_score": 8,
  "d3_trecho": "[trecho representativo do D3]",
  "d3_virou_aula": false,
  "d3_observacao": "Diagnóstico com trecho citado.",

  "cta_score": 4,
  "cta_trecho": "[trecho exato do CTA]",
  "cta_tipo_identificado": "Atração | Qualificação | Conversão",
  "cta_alinhado_objetivo": true,
  "cta_observacao": "Diagnóstico com trecho citado. Se desalinhado, explicar qual deveria ser.",

  "linguagem_score": 9,
  "linguagem_observacao": "Diagnóstico com exemplos citados da transcrição.",

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

  "pontos_fortes": "Diagnóstico preciso com trechos citados do que está funcionando.",
  "principal_gap": "O gap mais crítico com trecho citado. Use linguagem de urgência: 'Se continuar assim, seu conteúdo vai continuar flopado porque...' ou 'Esse erro está sabotando ativamente seus resultados porque...'. Cite o trecho exato do erro.",
  "proximo_passo": "Ação específica e cirúrgica com urgência. Ex: Reescreva o gancho AGORA como afirmação: em vez de 'Você já tentou...' (que faz o espectador desengajar imediatamente), use 'Toda pessoa que tenta X sem Y está cometendo o erro que ninguém fala.'",

  "nivel_alerta": "critico | importante | atencao",
  "headline_diagnostico": "Frase curta e impactante com viés negativo para exibir no topo do relatório. Exemplos: 'Encontramos 3 erros críticos que estão sabotando seu perfil' | 'Seu gancho está expulsando seguidores antes de 3 segundos' | 'Identificamos gaps importantes que estão custando vendas' | 'Atenção: seu conteúdo tem potencial, mas esses ajustes são urgentes'. Use sempre tom de alerta — nunca elogioso."
}}"""

    raw_text = ""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
        result = _parse_json_response(raw_text)

        logger.info(
            "Score de video calculado — gancho: %s, d1: %s, d2: %s, d3: %s, cta: %s, nivel: %s",
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
