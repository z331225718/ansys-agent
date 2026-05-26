from __future__ import annotations

import fnmatch
import re
from typing import Any, Iterable


def normalize_net_patterns(value: Any) -> list[str]:
    return parse_net_patterns(value)


def resolve_matching_nets(patterns: list[str], available_nets: list[str]) -> list[str]:
    return sorted(expand_net_patterns(patterns, available_nets))


def parse_net_patterns(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_pattern_string(value)
    if isinstance(value, Iterable):
        patterns: list[str] = []
        for item in value:
            patterns.extend(parse_net_patterns(item))
        return patterns
    return _split_pattern_string(str(value))


def expand_net_patterns(patterns: list[str], available_nets: list[str], *, case_sensitive: bool = False, fuzzy: bool = False) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    available_by_fold = {net.casefold(): net for net in available_nets}
    for raw_pattern in patterns:
        pattern = raw_pattern.strip()
        if not pattern:
            continue
        if _is_wildcard(pattern):
            for net in available_nets:
                if _pattern_matches(pattern, net, case_sensitive=case_sensitive) and net not in seen:
                    matched.append(net)
                    seen.add(net)
            continue
        exact = pattern if case_sensitive else available_by_fold.get(pattern.casefold())
        if exact is not None and exact in available_nets and exact not in seen:
            matched.append(exact)
            seen.add(exact)
    if not matched and fuzzy:
        matched = _closest_differential_nets(patterns, available_nets)
    return matched


def _split_pattern_string(value: str) -> list[str]:
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return [item.strip().strip("'\"") for item in text.split(",") if item.strip().strip("'\"")]


def _is_wildcard(pattern: str) -> bool:
    return bool(re.search(r"[*?\[\]]", pattern))


def _pattern_matches(pattern: str, net: str, *, case_sensitive: bool) -> bool:
    if case_sensitive:
        return fnmatch.fnmatchcase(net, pattern)
    return fnmatch.fnmatchcase(net.casefold(), pattern.casefold())


def _closest_differential_nets(patterns: list[str], available_nets: list[str]) -> list[str]:
    query_tokens = _meaningful_net_tokens(" ".join(patterns))
    if not query_tokens:
        return []
    scored = []
    for net in available_nets:
        tokens = _meaningful_net_tokens(net)
        score = len(query_tokens & tokens)
        if _net_polarity(net):
            score += 1
        if score:
            scored.append((score, net))
    if not scored:
        return []
    best_score = max(score for score, _ in scored)
    if best_score < 3:
        return []
    candidates = [net for score, net in scored if score == best_score]
    pairs: dict[str, dict[str, str]] = {}
    for net in candidates:
        polarity = _net_polarity(net)
        if not polarity:
            continue
        pairs.setdefault(_net_without_polarity(net), {})[polarity] = net
    complete_pairs = [pair for pair in pairs.values() if {"n", "p"}.issubset(pair)]
    if complete_pairs:
        pair = sorted(complete_pairs, key=lambda item: (item["n"], item["p"]))[0]
        ordered = []
        for net in available_nets:
            if net in {pair["n"], pair["p"]}:
                ordered.append(net)
        return ordered
    return sorted(candidates)


def _meaningful_net_tokens(value: str) -> set[str]:
    tokens = set()
    for token in re.split(r"[^A-Za-z0-9]+", value.casefold()):
        if len(token) >= 2 and not token.isdigit() and token not in {"net", "diff", "signal"}:
            tokens.add(token)
    return tokens


def _net_polarity(net: str) -> str:
    match = re.search(r"(?:^|[_+\-])([pn])$", net, flags=re.IGNORECASE)
    return match.group(1).casefold() if match else ""


def _net_without_polarity(net: str) -> str:
    return re.sub(r"[_+\-][pn]$", "", net, flags=re.IGNORECASE).casefold()
