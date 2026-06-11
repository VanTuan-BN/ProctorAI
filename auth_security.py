import bcrypt


_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


def is_password_hashed(value):
    text = str(value or "")
    return text.startswith(_BCRYPT_PREFIXES)


def hash_password(password):
    raw = str(password or "").encode("utf-8")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password, stored_password):
    raw_password = str(plain_password or "")
    raw_stored = str(stored_password or "")
    if not raw_stored:
        return False
    if is_password_hashed(raw_stored):
        try:
            return bcrypt.checkpw(raw_password.encode("utf-8"), raw_stored.encode("utf-8"))
        except ValueError:
            return False
    return raw_password == raw_stored


def verify_and_upgrade_password(cur, table_name, id_column, id_value, plain_password, stored_password, password_column="password"):
    if not verify_password(plain_password, stored_password):
        return False, False
    if is_password_hashed(stored_password):
        return True, False
    upgraded_hash = hash_password(plain_password)
    cur.execute(
        f"UPDATE {table_name} SET {password_column} = %s WHERE {id_column} = %s",
        (upgraded_hash, id_value),
    )
    return True, True