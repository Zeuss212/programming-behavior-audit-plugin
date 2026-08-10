def analyze_scores(scores, pass_score=60):
    count = len(scores)
    average = round(sum(scores) / count, 2)
    passed_count = sum(score >= pass_score for score in scores)
    return {
        "count": count,
        "average": average,
        "highest": max(scores),
        "lowest": min(scores),
        "pass_rate": round(passed_count / count * 100, 2),
    }


print(analyze_scores([]))
