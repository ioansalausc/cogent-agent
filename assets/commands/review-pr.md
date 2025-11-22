# Review Pull Request

Review the pull request with the given number: $ARGUMENTS

## Instructions

1. Fetch the PR details using `gh pr view $ARGUMENTS`
2. Get the diff using `gh pr diff $ARGUMENTS`
3. Analyze the changes for:
   - Code quality
   - Security issues
   - Performance concerns
   - Test coverage
4. Provide a structured review following the code-review skill guidelines
5. Optionally, add a review comment using `gh pr review`

## Expected Output

A comprehensive code review report with:
- Summary of changes
- Issues found (if any)
- Suggestions for improvement
- Overall recommendation (approve/request changes)
