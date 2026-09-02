from requirements_review_agent.models import AnalysisSubmission


def main() -> None:
    print(AnalysisSubmission.model_json_schema(sort_keys=True))


if __name__ == "__main__":
    main()
