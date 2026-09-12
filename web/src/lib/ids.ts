/**
 * A route parameter is whatever the address bar happens to contain. Checking
 * its shape before it reaches a request keeps a mistyped link from arriving at
 * the API as a 422 the user cannot act on -- and keeps the screen from firing
 * a request it already knows is malformed.
 */
export const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
