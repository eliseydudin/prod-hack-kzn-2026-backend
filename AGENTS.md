# Agents
1. Ask user for permission when making a new API route.
2. Define the table models in `/app/models`. If the public model and the table model have shared attributes, separate them into another one. For example if you have `UserTable` (in the database) and `User` (sent to the user via the API), create a `UserBase` class that has the shared attributes in it, and make `User` and `UserTable` inherit from `UserBase`.
3. When updating the database schema, update the file `docs/database.puml` too.
4. Don't write RFCs yourself, instead detail the changes to the user so that they can write it yourself.
5. Don't overdo tests. There's no need to be checking if pydantic validation works for example.
6. Only add comments to the code if its something hacky.
7. Don't touch `.gitlab-ci.yml`.
