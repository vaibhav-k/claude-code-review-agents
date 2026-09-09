public String fetchName(int id) throws SQLException {
    try (Connection conn = pool.getConnection();
         PreparedStatement ps = conn.prepareStatement("SELECT name FROM t WHERE id=?")) {
        ps.setInt(1, id);
        try (ResultSet rs = ps.executeQuery()) {
            return rs.next() ? rs.getString(1) : null;
        }
    }
}
