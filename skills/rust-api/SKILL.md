---
name: rust-api
description: Rust + Actix Web REST API development patterns
---

## Rust API Development

### Project Structure
```
src/
  main.rs          # Server startup, app config
  routes/          # Route handlers (mod.rs + per-resource)
  models/          # Database models (SQLx structs)
  errors.rs        # Custom error types, impl ResponseError
  middleware/       # Auth, logging, CORS
  config.rs        # Environment config with dotenv
```

### Error Handling
- Define `AppError` enum implementing `actix_web::ResponseError`
- Use `thiserror` for derive macro
- Never use `.unwrap()` in handlers — always propagate with `?`
- Return proper HTTP status codes (400 for validation, 401/403 for auth, 500 for internal)

### Authentication
- JWT tokens with `jsonwebtoken` crate
- Password hashing with `bcrypt`
- Auth middleware extracts user from token
- Refresh token rotation for security

### Database (SQLx)
- Use `sqlx::query_as!` macro for compile-time checked queries
- Connection pool via `PgPool` in app state
- Migrations with `sqlx migrate run`
- Always use transactions for multi-step operations

### Testing
- Use `actix_web::test` for integration tests
- Create test database, run migrations, seed data
- Test both happy path and error cases
- Use `#[sqlx::test]` for database-dependent tests
