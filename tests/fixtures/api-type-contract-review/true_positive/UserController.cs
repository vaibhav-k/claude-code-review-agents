// present in this diff, NOT updated
var user = _userService.FindUser(id);
return Ok(user.Name);
