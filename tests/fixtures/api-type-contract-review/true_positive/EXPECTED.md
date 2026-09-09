Expected finding:

[HIGH] UserController.cs:2 - FindUser now returns User? but caller dereferences without a null check
Impact: when _repo.Find(id) returns null, this line throws a NullReferenceException, turning a previously well-typed lookup miss into an unhandled 500.
Fix: handle the null case explicitly at the call site, e.g. if (user is null) return NotFound(); return Ok(user.Name);.
