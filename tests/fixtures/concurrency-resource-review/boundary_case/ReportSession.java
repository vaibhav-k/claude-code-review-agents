class ReportSession implements AutoCloseable {
    private final Connection conn = pool.getConnection();
    ResultSet run(String sql) throws SQLException {
        Statement stmt = conn.createStatement();
        stmt.closeOnCompletion(); // closes stmt once the returned ResultSet is closed/exhausted
        return stmt.executeQuery(sql);
    }
    public void close() throws SQLException { conn.close(); }
}
