// also updated in this same diff
var user = _userService.FindUser(id);
if (user is null) return NotFound();
return Ok(user.Name);
