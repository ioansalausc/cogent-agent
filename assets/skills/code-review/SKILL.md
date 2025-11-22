# Code Review Skill

This skill provides automated code review capabilities.

## When to Use

Activate this skill when:
- User asks for a code review
- PR review is requested
- Code quality assessment is needed

## Review Checklist

When reviewing code, check for:

### Code Quality
- [ ] Code follows project conventions
- [ ] Functions/methods are appropriately sized
- [ ] Variable names are descriptive
- [ ] No code duplication
- [ ] Proper error handling

### Security
- [ ] No hardcoded credentials
- [ ] Input validation present
- [ ] SQL injection prevention
- [ ] XSS prevention (for web code)
- [ ] Proper authentication/authorization

### Performance
- [ ] No obvious performance issues
- [ ] Efficient algorithms used
- [ ] Database queries optimized
- [ ] No memory leaks

### Testing
- [ ] Tests cover new functionality
- [ ] Edge cases considered
- [ ] Test names are descriptive

## Output Format

Provide feedback in the following format:

```markdown
## Code Review Summary

**Overall Assessment**: [APPROVE/REQUEST_CHANGES/NEEDS_DISCUSSION]

### Strengths
- Point 1
- Point 2

### Issues Found
1. **[Severity]** Description
   - Location: file:line
   - Suggestion: How to fix

### Suggestions for Improvement
- Suggestion 1
- Suggestion 2
```
